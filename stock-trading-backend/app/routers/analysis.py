"""Comprehensive analysis router - combines technical, fundamental, and sentiment."""

import json
from datetime import datetime
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import pandas as pd

from app.database import get_db
from app.indicator_engine import compute_all_indicators
from app.signal_engine import generate_signal
from app.fundamental_engine import get_fundamental_data
from app.news_engine import get_news_sentiment
from app import fyers_client
from app.brokerage_calc import (
    compare_intraday_vs_delivery,
    calc_max_intraday_quantity,
    calc_max_delivery_quantity,
)

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.get("/brokerage-comparison")
async def brokerage_comparison(
    buy_price: float = Query(..., gt=0, description="Entry / buy price per share"),
    sell_price: float = Query(..., gt=0, description="Exit / sell price per share"),
    qty: int = Query(1, ge=1, description="Quantity (shares)"),
    available_margin: Optional[float] = Query(
        None, ge=0, description="Available cash — used to compute max qty per mode",
    ),
):
    """Side-by-side intraday vs delivery comparison for a round-trip trade.

    Returns the full Fyers charges breakdown (brokerage, STT, exchange,
    SEBI, IPFT, stamp duty, GST) for both product types, the net profit
    after charges, a recommendation, and the max quantity the caller can
    afford in each mode given ``available_margin``.
    """
    result = compare_intraday_vs_delivery(buy_price, sell_price, qty)

    if available_margin is not None:
        result["max_qty"] = {
            "intraday": calc_max_intraday_quantity(available_margin, buy_price),
            "delivery": calc_max_delivery_quantity(available_margin, buy_price),
            "available_margin": round(float(available_margin), 2),
        }

    # Risk flag: intraday charges eat a huge fraction of the profit.
    gross = result.get("gross_profit", 0) or 0
    intra_charges = result["intraday"]["total_charges"]
    result["risk_assessment"] = {
        "intraday_charges_exceed_profit": bool(gross > 0 and intra_charges > gross),
        "intraday_charges_pct_of_gross": result["intraday"]["charges_pct_of_gross"],
        "delivery_charges_pct_of_gross": result["delivery"]["charges_pct_of_gross"],
        "prefer_delivery": bool(
            gross > 0 and intra_charges > 0.5 * gross
        ),
    }

    return result


@router.get("/comprehensive")
async def comprehensive_analysis(
    symbol: str = Query(..., description="Symbol to analyze (e.g. NSE:RELIANCE-EQ)"),
    timeframe: str = Query("1D", description="Timeframe"),
    instrument_type: str = Query("EQUITY", description="EQUITY, OPTION, FUTURE, INDEX"),
    db: AsyncSession = Depends(get_db),
):
    """Full comprehensive analysis: technical + fundamental + sentiment + PUT/CALL."""
    # 1. Fetch candle data — prefer Fyers live data when connected
    rows = None
    data_source = "database"

    if fyers_client.is_authenticated():
        days_map = {"1m": 7, "5m": 30, "15m": 60, "1D": 365, "1W": 730, "1Y": 365}
        days = days_map.get(timeframe, 365)
        fyers_candles = await fyers_client.get_historical_data_async(symbol, timeframe, days)
        if fyers_candles and len(fyers_candles) >= 10:
            data_source = "fyers_live"
            df = pd.DataFrame(fyers_candles)
            df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float)
            df["volume"] = df["volume"].astype(float)
            rows = True  # flag that we have data

    if rows is None:
        # Fallback to database
        query = text("""
            SELECT timestamp, open, high, low, close, volume
            FROM ohlcv_candles
            WHERE symbol = :symbol AND timeframe = :timeframe
            ORDER BY timestamp ASC
        """)
        result = await db.execute(query, {"symbol": symbol, "timeframe": timeframe})
        db_rows = result.fetchall()

        if not db_rows or len(db_rows) < 10:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "instrument_type": instrument_type,
                "signal": {"signal": "NEUTRAL", "confidence": 0, "reasons": ["Insufficient data"], "score": 0, "technical_score": 0, "fundamental_score": 0, "sentiment_score": 0, "weight_description": "", "fno_recommendation": None, "instrument_type": instrument_type},
                "indicators": {},
                "fundamental": None,
                "sentiment": None,
            }

        df = pd.DataFrame(db_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float)
        df["volume"] = df["volume"].astype(float)

    # 2. Compute technical indicators
    indicators = compute_all_indicators(df)

    # 3. Fetch fundamental data (only for EQUITY)
    fundamental = None
    if instrument_type in ("EQUITY",):
        try:
            fundamental = await get_fundamental_data(symbol)
        except Exception:
            fundamental = None

    # 4. Fetch news sentiment
    sentiment = None
    try:
        sentiment = await get_news_sentiment(symbol)
    except Exception:
        sentiment = None

    # 5. Generate unified signal, honouring Indicators Control toggles.
    from app.auto_trade_engine import _get_settings as _get_trading_settings
    _settings_peek = await _get_trading_settings() or {}
    signal = generate_signal(
        indicators=indicators,
        fundamental=fundamental,
        sentiment=sentiment,
        instrument_type=instrument_type,
        disabled_indicators=_settings_peek.get("disabled_indicators") or [],
    )

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "instrument_type": instrument_type,
        "signal": signal,
        "indicators": indicators,
        "fundamental": fundamental,
        "sentiment": sentiment,
        "data_source": data_source,
        "analysis_time": datetime.utcnow().isoformat(),
    }
