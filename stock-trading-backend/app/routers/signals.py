"""Signals API router - analyze symbols and generate explainable signals."""

import asyncio
import json
import logging
import time
from datetime import datetime
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any, Dict, Optional, Tuple
import pandas as pd

from app.database import get_db
from app.fundamental_engine import get_fundamental_data
from app.indicator_engine import compute_all_indicators
from app.news_engine import get_news_sentiment
from app.signal_engine import generate_signal
from app.auto_trade_engine import _get_settings as _get_trading_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/signals", tags=["signals"])

# --- Dashboard scan cache ---------------------------------------------------
# The /dashboard endpoint is hit by every open page on a short interval. Even
# when the auto-trade engine has no cached signals, the expensive path does up
# to 20 sequential Fyers historical-data calls + indicator computations per
# request. Cache the response per-timeframe so bursty polling collapses to
# one expensive scan every DASHBOARD_CACHE_TTL seconds.
_dashboard_cache: dict = {"data": None, "timestamp": 0.0, "timeframe": None, "segment": None}
DASHBOARD_CACHE_TTL = 30  # seconds

# --- Fundamental / sentiment caches -----------------------------------------
# The dashboard scans up to 20 symbols; the analyze endpoint is hit ad-hoc.
# yfinance fundamental + news pulls can each add hundreds of ms per symbol,
# so we cache them in-process with a TTL. Fundamentals change slowly (daily),
# news sentiment moves faster (we refresh a few times an hour).
_FUNDAMENTAL_TTL_SECS = 10 * 60  # 10 minutes
_SENTIMENT_TTL_SECS = 5 * 60     # 5 minutes
_fundamental_cache: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}
_sentiment_cache: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}


async def _safe_fetch_fundamental(symbol: str) -> Optional[Dict[str, Any]]:
    """TTL-cached fundamental fetch; swallows fetch errors."""
    now = time.time()
    hit = _fundamental_cache.get(symbol)
    if hit and hit[0] > now:
        return hit[1]
    try:
        data = await get_fundamental_data(symbol)
    except Exception as ex:
        logger.debug(f"Fundamental fetch failed for {symbol}: {ex}")
        return None
    _fundamental_cache[symbol] = (now + _FUNDAMENTAL_TTL_SECS, data)
    return data


async def _safe_fetch_sentiment(symbol: str) -> Optional[Dict[str, Any]]:
    """TTL-cached news sentiment fetch; swallows fetch errors."""
    now = time.time()
    hit = _sentiment_cache.get(symbol)
    if hit and hit[0] > now:
        return hit[1]
    try:
        data = await get_news_sentiment(symbol)
    except Exception as ex:
        logger.debug(f"Sentiment fetch failed for {symbol}: {ex}")
        return None
    _sentiment_cache[symbol] = (now + _SENTIMENT_TTL_SECS, data)
    return data


@router.get("/analyze")
async def analyze_symbol(
    symbol: str = Query(..., description="Symbol to analyze"),
    timeframe: str = Query("1D", description="Timeframe"),
    instrument_type: str = Query("EQUITY", description="EQUITY, OPTION, FUTURE, INDEX"),
    db: AsyncSession = Depends(get_db),
):
    """Analyze a symbol and return explainable signal with indicators."""
    # Fetch candle data
    query = text("""
        SELECT timestamp, open, high, low, close, volume
        FROM ohlcv_candles
        WHERE symbol = :symbol AND timeframe = :timeframe
        ORDER BY timestamp ASC
    """)
    result = await db.execute(query, {"symbol": symbol, "timeframe": timeframe})
    rows = result.fetchall()

    if not rows or len(rows) < 10:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "instrument_type": instrument_type,
            "signal": {"signal": "NEUTRAL", "confidence": 0, "reasons": ["Insufficient data"], "score": 0},
            "indicators": {},
        }

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float)
    df["volume"] = df["volume"].astype(float)

    # Compute indicators
    indicators = compute_all_indicators(df)

    # Fetch fundamental + sentiment concurrently so the signal reflects the
    # same 40/35/25 weighting as /api/analysis/comprehensive. Fundamentals
    # are only meaningful for cash equities; options/futures/index signals
    # stay technical-only.
    fundamental: Optional[Dict[str, Any]] = None
    sentiment: Optional[Dict[str, Any]] = None
    if instrument_type == "EQUITY":
        fundamental, sentiment = await asyncio.gather(
            _safe_fetch_fundamental(symbol),
            _safe_fetch_sentiment(symbol),
        )
    else:
        sentiment = await _safe_fetch_sentiment(symbol)

    # Generate unified signal (technical + fundamental + sentiment).
    # Honour the user's Indicators Control toggles so the /analyze screen
    # score matches what the auto-trader sees on the next scan.
    _settings_peek = await _get_trading_settings() or {}
    signal = generate_signal(
        indicators,
        fundamental=fundamental,
        sentiment=sentiment,
        instrument_type=instrument_type,
        disabled_indicators=_settings_peek.get("disabled_indicators") or [],
    )

    # Save signal to database if significant
    if abs(signal["score"]) >= 30:
        try:
            insert_q = text("""
                INSERT INTO signals (symbol, signal_time, signal_type, entry_price,
                    stop_loss, target_1, target_2, target_3, confidence_score, 
                    reasoning, status, timeframe, instrument_type)
                VALUES (:symbol, :signal_time, :signal_type, :entry_price,
                    :stop_loss, :target_1, :target_2, :target_3, :confidence,
                    :reasoning, 'OPEN', :timeframe, :instrument_type)
            """)
            await db.execute(insert_q, {
                "symbol": symbol,
                "signal_time": datetime.utcnow(),
                "signal_type": signal["signal"],
                "entry_price": signal.get("entry_price"),
                "stop_loss": signal.get("stop_loss"),
                "target_1": signal.get("target_1"),
                "target_2": signal.get("target_2"),
                "target_3": signal.get("target_3"),
                "confidence": signal["confidence"],
                "reasoning": json.dumps({"reasons": signal["reasons"]}),
                "timeframe": timeframe,
                "instrument_type": instrument_type,
            })
            await db.commit()
        except Exception:
            await db.rollback()

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "instrument_type": instrument_type,
        "signal": signal,
        "indicators": indicators,
    }


@router.get("/history")
async def list_signals(
    symbol: Optional[str] = Query(None),
    signal_type: Optional[str] = Query(None, alias="type"),
    status: Optional[str] = Query(None),
    limit: int = Query(100),
    db: AsyncSession = Depends(get_db),
):
    """List historical signals."""
    clauses = ["1=1"]
    params = {}

    if symbol:
        clauses.append("symbol = :symbol")
        params["symbol"] = symbol
    if signal_type:
        clauses.append("signal_type = :signal_type")
        params["signal_type"] = signal_type
    if status:
        clauses.append("status = :status")
        params["status"] = status

    where = " AND ".join(clauses)
    query = text(f"""
        SELECT id, symbol, signal_time, signal_type, entry_price,
               stop_loss, target_1, target_2, target_3,
               confidence_score, reasoning, status, exit_price, exit_time, 
               pnl_percent, timeframe, instrument_type
        FROM signals
        WHERE {where}
        ORDER BY signal_time DESC
        LIMIT :limit
    """)
    params["limit"] = limit

    result = await db.execute(query, params)
    rows = result.fetchall()

    return [
        {
            "id": r[0], "symbol": r[1],
            "signal_time": str(r[2]) if r[2] else None,
            "signal_type": r[3],
            "entry_price": float(r[4]) if r[4] else None,
            "stop_loss": float(r[5]) if r[5] else None,
            "target_1": float(r[6]) if r[6] else None,
            "target_2": float(r[7]) if r[7] else None,
            "target_3": float(r[8]) if r[8] else None,
            "confidence_score": float(r[9]) if r[9] else None,
            "reasoning": r[10],
            "status": r[11],
            "exit_price": float(r[12]) if r[12] else None,
            "exit_time": str(r[13]) if r[13] else None,
            "pnl_percent": float(r[14]) if r[14] else None,
            "timeframe": r[15],
            "instrument_type": r[16],
        }
        for r in rows
    ]


@router.get("/dashboard")
async def dashboard_scan(
    segment: Optional[str] = Query(None),
    timeframe: str = Query("1D", description="Timeframe: 1m, 5m, 15m, 1D"),
    limit: int = Query(200),
    db: AsyncSession = Depends(get_db),
):
    """Scan top 20 heatmap stocks (10 gainers + 10 losers) using the SAME signal engine as Analyze screen.
    
    Uses Fyers candle data + compute_all_indicators + generate_signal for consistent signals.
    Falls back to heatmap change_pct only when Fyers data is unavailable.
    If auto-trade engine has recent signals, returns those directly.
    """
    from app.heatmap_poller import heatmap_poller
    from app import fyers_client
    from app import auto_trade_engine

    # If auto-trade engine has recent signals, use those (already analyzed top 20)
    engine_signals = auto_trade_engine.get_last_signals()
    if engine_signals:
        return {"count": len(engine_signals), "instruments": engine_signals, "timeframe": timeframe}

    # Serve from TTL cache when possible (same timeframe + segment).
    now_ts = time.time()
    if (
        _dashboard_cache["data"] is not None
        and _dashboard_cache["timeframe"] == timeframe
        and _dashboard_cache["segment"] == segment
        and now_ts - _dashboard_cache["timestamp"] < DASHBOARD_CACHE_TTL
    ):
        return _dashboard_cache["data"]

    # Otherwise, get top 20 stocks (10 gainers + 10 losers) from heatmap
    gainers = heatmap_poller.get_top_gainers(10)
    losers = heatmap_poller.get_top_losers(10)
    
    if not gainers and not losers:
        try:
            await heatmap_poller.poll_heatmap()
            gainers = heatmap_poller.get_top_gainers(10)
            losers = heatmap_poller.get_top_losers(10)
        except Exception:
            pass

    # Combine and deduplicate
    seen = set()
    stocks = []
    for s in gainers + losers:
        sym = s["symbol"]
        if sym not in seen and s.get("ltp", 0) > 0:
            seen.add(sym)
            stocks.append(s)

    if not stocks:
        inst_query = text("SELECT symbol, name, segment, approx_price, exchange FROM instruments WHERE is_active = true ORDER BY name LIMIT :limit")
        result = await db.execute(inst_query, {"limit": limit})
        instruments = result.fetchall()
        stocks = [{"symbol": r[0], "display_symbol": r[1], "ltp": r[3] or 0, "change_pct": 0, "volume": 0} for r in instruments]

    # Fetch live Fyers prices for all stocks in batch (async, off event loop)
    live_prices = {}
    fyers_connected = fyers_client.is_authenticated()
    if fyers_connected:
        symbols = [s["symbol"] for s in stocks[:limit]]
        batch_size = 50
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            try:
                prices = await fyers_client.get_live_prices_batch_async(batch)
                live_prices.update(prices)
            except Exception:
                pass

    # Pre-fetch fundamental + sentiment for all scanned symbols concurrently
    # so generate_signal uses the 40/35/25 weighting consistently. The
    # _safe_fetch_* helpers use a TTL cache, so repeated polls of /dashboard
    # don't hammer yfinance once values are warm.
    scan_symbols = [s["symbol"] for s in stocks[:limit]]
    fundamental_results = await asyncio.gather(
        *(_safe_fetch_fundamental(sym) for sym in scan_symbols),
        return_exceptions=True,
    )
    sentiment_results = await asyncio.gather(
        *(_safe_fetch_sentiment(sym) for sym in scan_symbols),
        return_exceptions=True,
    )
    fundamentals_by_symbol = {
        sym: (val if not isinstance(val, BaseException) else None)
        for sym, val in zip(scan_symbols, fundamental_results)
    }
    sentiments_by_symbol = {
        sym: (val if not isinstance(val, BaseException) else None)
        for sym, val in zip(scan_symbols, sentiment_results)
    }

    # Honour per-user Indicators Control toggles on the dashboard scan too,
    # so what the user sees here agrees with the auto-trader.
    _dashboard_settings = await _get_trading_settings() or {}

    scan_results = []
    for stock in stocks[:limit]:
        symbol = stock["symbol"]
        display_symbol = stock.get("display_symbol", symbol.replace("NSE:", "").replace("-EQ", ""))
        
        fyers_data = live_prices.get(symbol, {})
        ltp = fyers_data.get("ltp", 0) or stock.get("ltp", 0)
        change_pct = fyers_data.get("change_pct", 0) or stock.get("change_pct", 0)
        volume = fyers_data.get("volume", 0) or stock.get("volume", 0)

        if not ltp or ltp <= 0:
            continue

        fundamental = fundamentals_by_symbol.get(symbol)
        sentiment = sentiments_by_symbol.get(symbol)

        # Use FULL indicator + signal engine (same as Analyze screen)
        signal_data = None
        indicators = {}
        if fyers_connected:
            try:
                candles = await fyers_client.get_historical_data_async(symbol, timeframe=timeframe, days_back=100)
                if candles and len(candles) >= 15:
                    df = pd.DataFrame(candles)
                    df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float)
                    df["volume"] = df["volume"].astype(float)
                    indicators = compute_all_indicators(df)
                    signal_data = generate_signal(
                        indicators,
                        fundamental=fundamental,
                        sentiment=sentiment,
                        instrument_type="EQUITY",
                        disabled_indicators=_dashboard_settings.get("disabled_indicators") or [],
                    )
            except Exception:
                pass

        if signal_data:
            signal_type = signal_data.get("signal", "NEUTRAL")
            confidence = signal_data.get("confidence", 0)
            score = signal_data.get("score", 0)
            stop_loss = signal_data.get("stop_loss")
            target_price = signal_data.get("target_1")
            reasons = signal_data.get("reasons", [])
        else:
            # Fallback: no Fyers candle data, use heatmap change_pct
            signal_type = "NEUTRAL"
            confidence = 0
            score = 0
            reasons = [f"Change: {change_pct:+.2f}%"]

            if change_pct > 2.0:
                signal_type = "STRONG BUY"
                confidence = min(85, 55 + abs(change_pct) * 5)
                score = min(85, abs(change_pct) * 12)
            elif change_pct > 0.5:
                signal_type = "BUY"
                confidence = min(70, 40 + abs(change_pct) * 10)
                score = min(65, abs(change_pct) * 18)
            elif change_pct > 0.1:
                signal_type = "WEAK BUY"
                confidence = min(50, 30 + abs(change_pct) * 12)
                score = min(45, abs(change_pct) * 22)
            elif change_pct < -2.0:
                signal_type = "STRONG SELL"
                confidence = min(85, 55 + abs(change_pct) * 5)
                score = -min(85, abs(change_pct) * 12)
            elif change_pct < -0.5:
                signal_type = "SELL"
                confidence = min(70, 40 + abs(change_pct) * 10)
                score = -min(65, abs(change_pct) * 18)
            elif change_pct < -0.1:
                signal_type = "WEAK SELL"
                confidence = min(50, 30 + abs(change_pct) * 12)
                score = -min(45, abs(change_pct) * 22)

            if "BUY" in signal_type:
                stop_loss = round(ltp * 0.985, 2)
                target_price = round(ltp * 1.02, 2)
            elif "SELL" in signal_type:
                stop_loss = round(ltp * 1.015, 2)
                target_price = round(ltp * 0.98, 2)
            else:
                stop_loss = round(ltp * 0.985, 2)
                target_price = round(ltp * 1.02, 2)

        scan_results.append({
            "symbol": symbol,
            "name": display_symbol,
            "ltp": round(ltp, 2),
            "change_pct": round(change_pct, 2),
            "volume": volume,
            "signal": signal_type,
            "confidence": round(confidence, 1),
            "score": round(score, 1),
            "stop_loss": stop_loss,
            "target_1": target_price,
            "timeframe": timeframe,
            "signal_time": datetime.utcnow().strftime("%H:%M:%S"),
            "reasons": reasons[:5],
            "rsi": indicators.get("rsi"),
            "macd_hist": indicators.get("macd_hist"),
            "supertrend_direction": indicators.get("supertrend_direction"),
        })

    # Sort by absolute score descending (strongest signals first)
    scan_results.sort(key=lambda x: abs(x.get("score", 0)), reverse=True)

    result = {"count": len(scan_results), "instruments": scan_results, "timeframe": timeframe}
    _dashboard_cache["data"] = result
    _dashboard_cache["timestamp"] = now_ts
    _dashboard_cache["timeframe"] = timeframe
    _dashboard_cache["segment"] = segment
    return result
