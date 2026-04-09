"""Auto Trade Engine v3 - Capital-aware, re-analysis, trailing profit.

Improvements over v2:
1. Capital limit enforcement: trades don't exceed available margin
2. Continuous re-analysis: open trades re-analyzed every cycle, SL/target modified if trend changes
3. Trailing profit protection: target moves upward when trade is in profit
4. Settings hot-reload: reads settings from DB every cycle (no restart needed)
5. Timeframe + analysis basis tracking: records which timeframe and analysis type was used
6. Guppy GMMA included in signal analysis
"""

import json
import math
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

import pandas as pd
from sqlalchemy import text

from app.database import async_session_factory
from app import fyers_client
from app.heatmap_poller import heatmap_poller
from app.indicator_engine import compute_all_indicators
from app.signal_engine import generate_signal

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
MAX_ACTIVE_TRADES = 5

# Trailing profit config
TRAILING_PROFIT_TRIGGER_PCT = 1.0   # Start trailing after 1% profit
TRAILING_PROFIT_STEP_PCT = 0.5      # Move target up by 0.5% each step

# Re-analysis config
RE_ANALYSIS_INTERVAL_SECS = 120     # Re-analyze open trades every 2 minutes

# Engine state
_engine_task: Optional[asyncio.Task] = None
_engine_running = False
_last_scan_time: Optional[datetime] = None
_last_monitor_time: Optional[datetime] = None
_last_reanalysis_time: Optional[datetime] = None
_auto_trade_log: list = []
_daily_target_met = False

# SSE subscribers: list of asyncio.Queue objects
_sse_subscribers: list = []

# Last analyzed signals (shared with signals dashboard)
_last_signals: list = []

# Cached settings (refreshed every cycle)
_cached_settings: Optional[dict] = None


# --- SSE Event Bus -----------------------------------------------------------

def subscribe_sse() -> asyncio.Queue:
    """Subscribe to auto-trade SSE events. Returns a queue."""
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    _sse_subscribers.append(q)
    return q


def unsubscribe_sse(q: asyncio.Queue):
    """Remove an SSE subscriber."""
    if q in _sse_subscribers:
        _sse_subscribers.remove(q)


def _push_event(event_type: str, data: dict):
    """Push an event to all SSE subscribers."""
    payload = {"type": event_type, "data": data, "time": datetime.now(IST).isoformat()}
    dead = []
    for q in _sse_subscribers:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _sse_subscribers.remove(q)


def get_last_signals() -> list:
    """Return the last computed signals for the signals dashboard."""
    return list(_last_signals)


def _add_log(action: str, symbol: str, details: str):
    """Add to in-memory log."""
    global _auto_trade_log
    _auto_trade_log.append({
        "time": datetime.now(IST).isoformat(),
        "action": action,
        "symbol": symbol,
        "details": details,
    })
    if len(_auto_trade_log) > 200:
        _auto_trade_log = _auto_trade_log[-200:]


def is_market_open() -> bool:
    """Check if NSE market is currently open."""
    now = datetime.now(IST)
    day = now.weekday()  # 0=Mon, 6=Sun
    if day >= 5:  # Sat/Sun
        return False
    total_mins = now.hour * 60 + now.minute
    return 555 <= total_mins <= 930  # 9:15 AM to 3:30 PM IST


# --- Settings (hot-reload) ---------------------------------------------------

async def _get_settings() -> Optional[dict]:
    """Get trading settings from DB. Called every cycle for hot-reload."""
    global _cached_settings
    try:
        async with async_session_factory() as db:
            result = await db.execute(text("SELECT * FROM trading_settings WHERE id=1"))
            row = result.mappings().first()
            if row:
                _cached_settings = dict(row)
                return _cached_settings
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")
    return _cached_settings  # Return cached if DB fails


# --- Capital & Margin ---------------------------------------------------------

async def _get_available_margin(settings: dict) -> float:
    """Calculate available margin = simulated_capital + total_realized_pnl - open_exposure."""
    capital = float(settings.get("simulated_capital", 100000))
    async with async_session_factory() as db:
        pnl_result = await db.execute(text(
            "SELECT COALESCE(SUM(pnl_amount), 0) as total_pnl "
            "FROM paper_trades WHERE status != 'OPEN'"
        ))
        total_pnl = float(pnl_result.scalar() or 0)

        exposure_result = await db.execute(text(
            "SELECT COALESCE(SUM(entry_price * quantity), 0) as exposure "
            "FROM paper_trades WHERE status = 'OPEN'"
        ))
        open_exposure = float(exposure_result.scalar() or 0)

    available = capital + total_pnl - open_exposure
    return max(available, 0)


async def _get_open_trade_count() -> int:
    """Get count of open paper trades."""
    async with async_session_factory() as db:
        result = await db.execute(text("SELECT COUNT(*) FROM paper_trades WHERE status = 'OPEN'"))
        return result.scalar() or 0


async def _has_open_trade_for_symbol(symbol: str) -> bool:
    """Check if there is already an open trade for this symbol."""
    async with async_session_factory() as db:
        result = await db.execute(
            text("SELECT COUNT(*) FROM paper_trades WHERE symbol = :symbol AND status = 'OPEN'"),
            {"symbol": symbol}
        )
        return (result.scalar() or 0) > 0


async def _calculate_quantity(
    entry_price: float, sl_pct: float, tgt_pct: float,
    settings: dict, available_margin: float
) -> int:
    """Calculate optimal trade quantity based on settings AND available margin.

    Capital limit enforcement: quantity * entry_price must not exceed available_margin.
    """
    max_loss = abs(settings.get("day_max_loss_paper", 1000))
    profit_target = settings.get("day_profit_target_paper", 2000)

    sl_per_share = entry_price * sl_pct / 100.0
    target_per_share = entry_price * tgt_pct / 100.0

    if sl_per_share <= 0 or target_per_share <= 0:
        if entry_price <= available_margin:
            return 1
        return 0

    qty_from_loss = max_loss / sl_per_share
    qty_from_profit = profit_target / target_per_share
    qty_from_margin = available_margin / entry_price if entry_price > 0 else 0

    optimal_qty = int(math.floor(min(qty_from_loss, qty_from_profit, qty_from_margin)))

    if optimal_qty <= 0:
        logger.info(f"Capital limit: qty=0 (margin={available_margin:.0f}, price={entry_price:.2f})")
        return 0

    return optimal_qty


# --- Analysis -----------------------------------------------------------------

async def _analyze_stock(stock: dict, timeframe: str = "1D") -> Optional[dict]:
    """Run full indicator + signal engine on a single stock.

    Returns signal dict with analysis_basis and analyzed_timeframe, or None.
    """
    symbol = stock["symbol"]
    ltp = stock.get("ltp", 0)
    change_pct = stock.get("change_pct", 0)

    if fyers_client.is_authenticated():
        try:
            prices = fyers_client.get_live_prices_batch([symbol])
            if prices and symbol in prices:
                ltp = prices[symbol].get("ltp", ltp)
        except Exception:
            pass

    tf_days = {"1m": 5, "5m": 10, "15m": 20, "1D": 100}
    days_back = tf_days.get(timeframe, 100)

    signal_data = None
    analysis_basis = "heatmap_fallback"

    if fyers_client.is_authenticated():
        try:
            candles = fyers_client.get_historical_data(symbol, timeframe=timeframe, days_back=days_back)
            if candles and len(candles) >= 15:
                df = pd.DataFrame(candles)
                df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float)
                df["volume"] = df["volume"].astype(float)
                indicators = compute_all_indicators(df)
                signal_data = generate_signal(indicators, instrument_type="EQUITY")

                has_tech = signal_data.get("technical_score", 0) != 0
                has_fund = signal_data.get("fundamental_score", 0) != 0
                has_sent = signal_data.get("sentiment_score", 0) != 0

                if has_tech and has_fund and has_sent:
                    analysis_basis = "technical+fundamental+sentiment"
                elif has_tech and has_fund:
                    analysis_basis = "technical+fundamental"
                elif has_tech:
                    analysis_basis = "technical"
                else:
                    analysis_basis = "combined"

        except Exception as ex:
            logger.debug(f"Full analysis failed for {symbol}: {ex}")

    if not signal_data:
        signal_type = "NEUTRAL"
        confidence = 0.0
        score = 0.0

        if change_pct > 2.0:
            signal_type = "STRONG BUY"
            confidence = min(80.0, 50 + change_pct * 5)
            score = min(80.0, change_pct * 10)
        elif change_pct > 0.5:
            signal_type = "BUY"
            confidence = min(65.0, 40 + change_pct * 8)
            score = min(60.0, change_pct * 15)
        elif change_pct < -2.0:
            signal_type = "STRONG SELL"
            confidence = min(80.0, 50 + abs(change_pct) * 5)
            score = -min(80.0, abs(change_pct) * 10)
        elif change_pct < -0.5:
            signal_type = "SELL"
            confidence = min(65.0, 40 + abs(change_pct) * 8)
            score = -min(60.0, abs(change_pct) * 15)

        if signal_type == "NEUTRAL":
            return None

        sl_pct_val = 1.5
        tgt_pct_val = 2.0
        if "BUY" in signal_type:
            sl = round(ltp * (1 - sl_pct_val / 100), 2)
            tgt = round(ltp * (1 + tgt_pct_val / 100), 2)
        else:
            sl = round(ltp * (1 + sl_pct_val / 100), 2)
            tgt = round(ltp * (1 - tgt_pct_val / 100), 2)

        signal_data = {
            "signal": signal_type,
            "entry_price": ltp,
            "stop_loss": sl,
            "target_1": tgt,
            "confidence": round(confidence, 1),
            "score": round(score, 1),
            "reasons": [f"Change: {change_pct:+.2f}%", f"Signal: {signal_type}"],
        }
        analysis_basis = "heatmap_fallback"

    signal_data["entry_price"] = ltp

    return {
        "symbol": symbol,
        "ltp": ltp,
        "change_pct": change_pct,
        "signal_data": signal_data,
        "analysis_basis": analysis_basis,
        "analyzed_timeframe": timeframe,
    }


# --- Trade Placement ----------------------------------------------------------

async def _place_auto_trade(
    symbol: str, signal_data: dict, settings: dict,
    analysis_basis: str = "technical", analyzed_timeframe: str = "1D"
) -> Optional[int]:
    """Auto-place a paper trade based on signal. Returns trade_id or None.

    Enforces capital limit before placing.
    """
    try:
        signal_type = signal_data.get("signal", "NEUTRAL")
        entry_price = signal_data.get("entry_price", 0)
        stop_loss = signal_data.get("stop_loss")
        target = signal_data.get("target_1") or signal_data.get("target")
        confidence = signal_data.get("confidence", 0)
        score = signal_data.get("score", 0)
        reasons = signal_data.get("reasons", [])

        if not entry_price or entry_price <= 0:
            return None

        if "BUY" in signal_type.upper():
            side = "BUY"
        elif "SELL" in signal_type.upper():
            side = "SELL"
        else:
            return None

        sl_pct = settings.get("default_sl_percent", 1.5)
        tgt_pct = settings.get("default_target_percent", 2.0)

        if stop_loss and entry_price:
            sl_pct = abs((entry_price - stop_loss) / entry_price * 100)
        if target and entry_price:
            tgt_pct = abs((target - entry_price) / entry_price * 100)

        # CAPITAL LIMIT ENFORCEMENT
        available_margin = await _get_available_margin(settings)
        quantity = await _calculate_quantity(entry_price, sl_pct, tgt_pct, settings, available_margin)

        if quantity <= 0:
            _add_log("CAPITAL_LIMIT", symbol,
                     f"Rejected: margin={available_margin:.0f}, price={entry_price:.2f}")
            _push_event("CAPITAL_LIMIT", {
                "symbol": symbol,
                "available_margin": round(available_margin, 2),
                "required": round(entry_price, 2),
                "message": "Insufficient capital to place trade"
            })
            return None

        trade_cost = entry_price * quantity
        if trade_cost > available_margin:
            quantity = int(math.floor(available_margin / entry_price))
            if quantity <= 0:
                _add_log("CAPITAL_LIMIT", symbol,
                         f"Rejected after margin check: margin={available_margin:.0f}")
                return None
            trade_cost = entry_price * quantity

        snapshot = {
            "auto_trade": True,
            "signal_type": signal_type,
            "score": score,
            "analysis_basis": analysis_basis,
            "analyzed_timeframe": analyzed_timeframe,
            "available_margin_at_entry": round(available_margin, 2),
            "trade_cost": round(trade_cost, 2),
        }

        full_reasons = list(reasons) if isinstance(reasons, list) else [str(reasons)]
        full_reasons.insert(0, f"[{analyzed_timeframe}] {analysis_basis}")

        async with async_session_factory() as db:
            result = await db.execute(text(
                "INSERT INTO paper_trades (symbol, instrument_type, timeframe, side, entry_price, "
                "entry_time, quantity, stop_loss, target, status, signal_confidence, "
                "signal_reasons, indicators_snapshot, is_auto_trade) "
                "VALUES (:symbol, 'EQUITY', :timeframe, :side, :entry_price, "
                ":entry_time, :quantity, :stop_loss, :target, 'OPEN', :signal_confidence, "
                ":signal_reasons, :indicators_snapshot, true) "
                "RETURNING id"
            ), {
                "symbol": symbol,
                "side": side,
                "entry_price": entry_price,
                "entry_time": datetime.now(IST).replace(tzinfo=None),
                "quantity": quantity,
                "stop_loss": stop_loss,
                "target": target,
                "timeframe": analyzed_timeframe,
                "signal_confidence": confidence,
                "signal_reasons": json.dumps(full_reasons),
                "indicators_snapshot": json.dumps(snapshot),
            })
            await db.commit()
            trade_id = result.scalar()

        _add_log("AUTO_PLACE", symbol,
                 f"Trade #{trade_id}: {side} {quantity}x @ {entry_price}, SL={stop_loss}, "
                 f"Target={target}, Signal={signal_type}, Basis={analysis_basis}, TF={analyzed_timeframe}, "
                 f"Cost={trade_cost:.0f}, Margin={available_margin:.0f}")
        logger.info(f"Auto-placed trade #{trade_id}: {side} {symbol} {quantity}x @ {entry_price} "
                     f"[{analyzed_timeframe}/{analysis_basis}]")

        _push_event("TRADE_PLACED", {
            "trade_id": trade_id,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "target": target,
            "signal": signal_type,
            "score": score,
            "analysis_basis": analysis_basis,
            "analyzed_timeframe": analyzed_timeframe,
        })
        return trade_id

    except Exception as e:
        logger.error(f"Auto-place trade error for {symbol}: {e}")
        _add_log("ERROR", symbol, f"Failed to place trade: {e}")
        return None


# --- Heatmap Top 20 -----------------------------------------------------------

async def _get_top20_stocks() -> list:
    """Get top 10 gainers + top 10 losers from heatmap = 20 stocks max."""
    gainers = heatmap_poller.get_top_gainers(10)
    losers = heatmap_poller.get_top_losers(10)
    if not gainers and not losers:
        try:
            await heatmap_poller.poll_heatmap()
            gainers = heatmap_poller.get_top_gainers(10)
            losers = heatmap_poller.get_top_losers(10)
        except Exception as e:
            logger.warning(f"Heatmap poll failed: {e}")

    seen: set = set()
    top20: list = []
    for s in gainers + losers:
        sym = s["symbol"]
        if sym not in seen and s.get("ltp", 0) > 0:
            seen.add(sym)
            top20.append(s)
    return top20


# --- Daily Limits Check -------------------------------------------------------

async def _check_daily_limits(settings: dict) -> Dict[str, Any]:
    """Check daily profit target AND max loss limit from FRESH settings."""
    global _daily_target_met

    profit_target = settings.get("day_profit_target_paper", 0)
    max_loss = abs(settings.get("day_max_loss_paper", 0))
    today = datetime.now(IST).date()

    async with async_session_factory() as db:
        result = await db.execute(text(
            "SELECT COALESCE(SUM(pnl_amount), 0) AS total_pnl "
            "FROM paper_trades "
            "WHERE status = 'CLOSED' AND DATE(exit_time) = :today"
        ), {"today": today})
        total_pnl = float(result.scalar() or 0)

    if profit_target > 0 and total_pnl >= profit_target:
        _daily_target_met = True
        _push_event("DAILY_TARGET_MET", {"total_pnl": round(total_pnl, 2), "target": profit_target})
        _add_log("TARGET_MET", "", f"Daily profit target met: {total_pnl:.2f} >= {profit_target}")
        return {"trading_allowed": False, "stop_reason": "PROFIT_TARGET_MET", "total_pnl": total_pnl}

    if max_loss > 0 and total_pnl <= -max_loss:
        _push_event("MAX_LOSS_HIT", {
            "total_pnl": round(total_pnl, 2),
            "max_loss": max_loss,
            "message": f"Daily loss limit reached: {total_pnl:.2f} (limit: -{max_loss})"
        })
        _add_log("MAX_LOSS", "", f"Daily max loss hit: {total_pnl:.2f} <= -{max_loss}")
        return {"trading_allowed": False, "stop_reason": "MAX_LOSS_HIT", "total_pnl": total_pnl}

    _daily_target_met = False
    return {"trading_allowed": True, "stop_reason": None, "total_pnl": total_pnl}


# --- Scan & Trade -------------------------------------------------------------

async def _scan_and_trade() -> int:
    """Main scan: get top 20 from heatmap, analyze, trade top picks."""
    global _last_scan_time, _last_signals

    settings = await _get_settings()
    if not settings:
        logger.warning("Auto-trade: Settings not initialized")
        return 0

    limits = await _check_daily_limits(settings)
    if not limits["trading_allowed"]:
        sr = limits["stop_reason"]
        tp = limits["total_pnl"]
        logger.info(f"Auto-trade: {sr}, P&L={tp:.2f}")
        return 0

    top20 = await _get_top20_stocks()
    if not top20:
        logger.info("Auto-trade: No stocks in heatmap")
        return 0

    _add_log("SCAN_START", "", f"Scanning top {len(top20)} stocks (10 gainers + 10 losers)")

    analyzed: list = []
    for stock in top20:
        result = await _analyze_stock(stock, timeframe="1D")
        if result:
            analyzed.append(result)
        await asyncio.sleep(0.3)

    signals_list = []
    for item in analyzed:
        sd = item["signal_data"]
        signals_list.append({
            "symbol": item["symbol"],
            "ltp": item["ltp"],
            "change_pct": item.get("change_pct", 0),
            "signal": sd.get("signal", "NEUTRAL"),
            "score": sd.get("score", 0),
            "confidence": sd.get("confidence", 0),
            "entry_price": sd.get("entry_price", 0),
            "stop_loss": sd.get("stop_loss"),
            "target": sd.get("target_1") or sd.get("target"),
            "reasons": sd.get("reasons", []),
            "analysis_basis": item.get("analysis_basis", "unknown"),
            "analyzed_timeframe": item.get("analyzed_timeframe", "1D"),
        })

    signals_list.sort(key=lambda x: abs(x.get("score", 0)), reverse=True)
    _last_signals = signals_list
    _push_event("SIGNALS_UPDATED", {"count": len(signals_list)})

    open_count = await _get_open_trade_count()
    if open_count >= MAX_ACTIVE_TRADES:
        _add_log("LIMIT", "", f"Max active trades ({MAX_ACTIVE_TRADES}) reached")
        _last_scan_time = datetime.now(IST)
        return 0

    slots = MAX_ACTIVE_TRADES - open_count
    tradeable = [
        a for a in analyzed
        if a["signal_data"].get("signal", "NEUTRAL") != "NEUTRAL"
    ]
    tradeable.sort(key=lambda x: abs(x["signal_data"].get("score", 0)), reverse=True)

    placed = 0
    for pick in tradeable[:slots]:
        sym = pick["symbol"]
        if await _has_open_trade_for_symbol(sym):
            continue
        trade_id = await _place_auto_trade(
            sym, pick["signal_data"], settings,
            analysis_basis=pick.get("analysis_basis", "technical"),
            analyzed_timeframe=pick.get("analyzed_timeframe", "1D")
        )
        if trade_id:
            placed += 1
            if placed >= slots:
                break

    _last_scan_time = datetime.now(IST)
    _add_log("SCAN_COMPLETE", "",
             f"Analyzed {len(analyzed)}/{len(top20)}, placed {placed} trades (slots: {slots})")
    logger.info(f"Auto-trade scan: {len(top20)} stocks, {len(analyzed)} analyzed, {placed} placed")
    return placed


# --- Monitor: SL/Target + Trailing Profit + Re-Analysis ----------------------

async def _monitor_open_trades(settings: dict) -> int:
    """Monitor open trades: auto-close on SL/target, trailing profit, re-analysis."""
    global _last_monitor_time, _last_reanalysis_time

    if not fyers_client.is_authenticated():
        return 0

    async with async_session_factory() as db:
        result = await db.execute(text(
            "SELECT id, symbol, side, entry_price, quantity, stop_loss, target, "
            "entry_time, indicators_snapshot "
            "FROM paper_trades WHERE status = 'OPEN'"
        ))
        open_trades = result.fetchall()

    if not open_trades:
        return 0

    symbols = list(set(t[1] for t in open_trades))
    all_prices: dict = {}
    batch_size = 50
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        try:
            prices = fyers_client.get_live_prices_batch(batch)
            all_prices.update(prices)
        except Exception as e:
            logger.error(f"Monitor: Failed to fetch prices: {e}")

    if not all_prices:
        return 0

    now = datetime.now(IST)
    do_reanalysis = False
    if _last_reanalysis_time is None or (now - _last_reanalysis_time).total_seconds() >= RE_ANALYSIS_INTERVAL_SECS:
        do_reanalysis = True
        _last_reanalysis_time = now

    closed_count = 0
    for trade in open_trades:
        trade_id = trade[0]
        symbol = trade[1]
        side = trade[2]
        entry_price = float(trade[3])
        quantity = trade[4] or 1
        stop_loss = float(trade[5]) if trade[5] else None
        target = float(trade[6]) if trade[6] else None
        entry_time = trade[7]

        price_data = all_prices.get(symbol)
        if not price_data:
            continue

        ltp = price_data.get("ltp", 0)
        if ltp <= 0:
            continue

        # --- Trailing Profit Protection ---
        if target and stop_loss:
            if side == "BUY":
                profit_pct = (ltp - entry_price) / entry_price * 100
                if profit_pct >= TRAILING_PROFIT_TRIGGER_PCT:
                    new_target = round(ltp * (1 + TRAILING_PROFIT_STEP_PCT / 100), 2)
                    if new_target > target:
                        old_target = target
                        target = new_target
                        new_sl = round(max(stop_loss, entry_price * (1 + TRAILING_PROFIT_TRIGGER_PCT / 200)), 2)
                        if new_sl > stop_loss:
                            stop_loss = new_sl

                        async with async_session_factory() as db:
                            await db.execute(text(
                                "UPDATE paper_trades SET target = :target, stop_loss = :stop_loss "
                                "WHERE id = :id AND status = 'OPEN'"
                            ), {"target": target, "stop_loss": stop_loss, "id": trade_id})
                            await db.commit()

                        _add_log("TRAILING_PROFIT", symbol,
                                 f"Trade #{trade_id}: Target {old_target}->{target}, "
                                 f"SL tightened to {stop_loss}, profit={profit_pct:.1f}%")
                        _push_event("TRADE_MODIFIED", {
                            "trade_id": trade_id, "symbol": symbol,
                            "new_target": target, "new_stop_loss": stop_loss,
                            "reason": "trailing_profit",
                            "profit_pct": round(profit_pct, 2),
                        })

            elif side == "SELL":
                profit_pct = (entry_price - ltp) / entry_price * 100
                if profit_pct >= TRAILING_PROFIT_TRIGGER_PCT:
                    new_target = round(ltp * (1 - TRAILING_PROFIT_STEP_PCT / 100), 2)
                    if new_target < target:
                        old_target = target
                        target = new_target
                        new_sl = round(min(stop_loss, entry_price * (1 - TRAILING_PROFIT_TRIGGER_PCT / 200)), 2)
                        if new_sl < stop_loss:
                            stop_loss = new_sl

                        async with async_session_factory() as db:
                            await db.execute(text(
                                "UPDATE paper_trades SET target = :target, stop_loss = :stop_loss "
                                "WHERE id = :id AND status = 'OPEN'"
                            ), {"target": target, "stop_loss": stop_loss, "id": trade_id})
                            await db.commit()

                        _add_log("TRAILING_PROFIT", symbol,
                                 f"Trade #{trade_id}: Target {old_target}->{target}, "
                                 f"SL tightened to {stop_loss}, profit={profit_pct:.1f}%")
                        _push_event("TRADE_MODIFIED", {
                            "trade_id": trade_id, "symbol": symbol,
                            "new_target": target, "new_stop_loss": stop_loss,
                            "reason": "trailing_profit",
                            "profit_pct": round(profit_pct, 2),
                        })

        # --- Re-Analysis ---
        if do_reanalysis and entry_time:
            try:
                stock_data = {"symbol": symbol, "ltp": ltp, "change_pct": 0}
                reanalysis = await _analyze_stock(stock_data, timeframe="15m")
                if reanalysis:
                    new_signal = reanalysis["signal_data"].get("signal", "NEUTRAL")
                    new_score = reanalysis["signal_data"].get("score", 0)
                    new_sl = reanalysis["signal_data"].get("stop_loss")
                    new_tgt = reanalysis["signal_data"].get("target_1") or reanalysis["signal_data"].get("target")

                    is_reversal = False
                    if side == "BUY" and "SELL" in new_signal.upper():
                        is_reversal = True
                    elif side == "SELL" and "BUY" in new_signal.upper():
                        is_reversal = True

                    if is_reversal and abs(new_score) >= 20:
                        if side == "BUY":
                            tighter_sl = round(ltp * 0.998, 2)
                        else:
                            tighter_sl = round(ltp * 1.002, 2)

                        async with async_session_factory() as db:
                            await db.execute(text(
                                "UPDATE paper_trades SET stop_loss = :stop_loss "
                                "WHERE id = :id AND status = 'OPEN'"
                            ), {"stop_loss": tighter_sl, "id": trade_id})
                            await db.commit()

                        _add_log("RE_ANALYSIS", symbol,
                                 f"Trade #{trade_id}: Trend reversal ({new_signal}, score={new_score}). "
                                 f"SL tightened to {tighter_sl}")
                        _push_event("TRADE_MODIFIED", {
                            "trade_id": trade_id, "symbol": symbol,
                            "new_stop_loss": tighter_sl,
                            "reason": "trend_reversal",
                            "new_signal": new_signal, "new_score": new_score,
                        })
                        stop_loss = tighter_sl

                    elif not is_reversal and new_sl and new_tgt:
                        updated = False
                        if side == "BUY":
                            if new_sl and stop_loss and new_sl > stop_loss:
                                stop_loss = new_sl
                                updated = True
                            if new_tgt and target and new_tgt > target:
                                target = new_tgt
                                updated = True
                        elif side == "SELL":
                            if new_sl and stop_loss and new_sl < stop_loss:
                                stop_loss = new_sl
                                updated = True
                            if new_tgt and target and new_tgt < target:
                                target = new_tgt
                                updated = True

                        if updated:
                            async with async_session_factory() as db:
                                await db.execute(text(
                                    "UPDATE paper_trades SET stop_loss = :stop_loss, target = :target "
                                    "WHERE id = :id AND status = 'OPEN'"
                                ), {"stop_loss": stop_loss, "target": target, "id": trade_id})
                                await db.commit()

                            _add_log("RE_ANALYSIS", symbol,
                                     f"Trade #{trade_id}: SL/Target updated (SL={stop_loss}, Target={target})")
                            _push_event("TRADE_MODIFIED", {
                                "trade_id": trade_id, "symbol": symbol,
                                "new_stop_loss": stop_loss, "new_target": target,
                                "reason": "reanalysis_update",
                            })
            except Exception as e:
                logger.debug(f"Re-analysis failed for trade #{trade_id} ({symbol}): {e}")

        # --- SL / Target Hit Check ---
        exit_reason = None
        exit_price = ltp

        if side == "BUY":
            if stop_loss and ltp <= stop_loss:
                exit_reason = "AUTO_SL_HIT"
                exit_price = stop_loss
            elif target and ltp >= target:
                exit_reason = "AUTO_TARGET_HIT"
                exit_price = target
        elif side == "SELL":
            if stop_loss and ltp >= stop_loss:
                exit_reason = "AUTO_SL_HIT"
                exit_price = stop_loss
            elif target and ltp <= target:
                exit_reason = "AUTO_TARGET_HIT"
                exit_price = target

        if exit_reason:
            try:
                if side == "BUY":
                    pnl_pct = (exit_price - entry_price) / entry_price * 100
                    pnl_amount = (exit_price - entry_price) * quantity
                else:
                    pnl_pct = (entry_price - exit_price) / entry_price * 100
                    pnl_amount = (entry_price - exit_price) * quantity

                result_str = "WIN" if pnl_pct > 0 else ("LOSS" if pnl_pct < 0 else "BREAKEVEN")

                async with async_session_factory() as db:
                    await db.execute(text(
                        "UPDATE paper_trades SET "
                        "exit_price = :exit_price, exit_time = :exit_time, "
                        "status = 'CLOSED', result = :result, "
                        "pnl_percent = :pnl_pct, pnl_amount = :pnl_amount, "
                        "exit_reason = :exit_reason "
                        "WHERE id = :id AND status = 'OPEN'"
                    ), {
                        "exit_price": round(exit_price, 2),
                        "exit_time": datetime.now(IST).replace(tzinfo=None),
                        "result": result_str,
                        "pnl_pct": round(pnl_pct, 2),
                        "pnl_amount": round(pnl_amount, 2),
                        "exit_reason": exit_reason,
                        "id": trade_id,
                    })
                    await db.commit()

                closed_count += 1
                _add_log("AUTO_CLOSE", symbol,
                         f"Trade #{trade_id}: {exit_reason}, Exit={exit_price}, "
                         f"P&L={pnl_amount:+.2f} ({pnl_pct:+.2f}%)")
                logger.info(f"Auto-closed trade #{trade_id}: {exit_reason} @ {exit_price}, "
                             f"P&L={pnl_amount:+.2f}")

                _push_event("TRADE_CLOSED", {
                    "trade_id": trade_id,
                    "symbol": symbol,
                    "side": side,
                    "exit_price": round(exit_price, 2),
                    "exit_reason": exit_reason,
                    "pnl_amount": round(pnl_amount, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "result": result_str,
                })

            except Exception as e:
                logger.error(f"Auto-close error for trade #{trade_id}: {e}")
                _add_log("ERROR", symbol, f"Failed to close trade #{trade_id}: {e}")

    _last_monitor_time = datetime.now(IST)
    if closed_count > 0:
        _add_log("MONITOR", "", f"Auto-closed {closed_count} trades")
    return closed_count


# --- Engine Loop (HOT-RELOAD settings every cycle) ----------------------------

async def _engine_loop():
    """Unified engine loop with HOT-RELOAD settings every cycle."""
    global _engine_running, _daily_target_met
    _engine_running = True
    _daily_target_met = False
    logger.info("Auto-trade engine v3 started (capital-aware, trailing profit, re-analysis)")
    _add_log("ENGINE", "", "Auto-trade engine v3 started (hot-reload, trailing profit, re-analysis)")

    while _engine_running:
        try:
            # HOT-RELOAD: Read fresh settings from DB EVERY cycle
            settings = await _get_settings()
            if not settings:
                await asyncio.sleep(10)
                continue

            auto_enabled = settings.get("auto_trade_enabled", False)
            market_open = is_market_open()

            if auto_enabled and market_open:
                limits = await _check_daily_limits(settings)

                if limits["trading_allowed"]:
                    placed = await _scan_and_trade()

                    for _ in range(30):  # 30 * 2s = 60s
                        if not _engine_running:
                            break
                        # HOT-RELOAD inside monitor loop too
                        settings = await _get_settings()
                        if not settings:
                            break

                        limits = await _check_daily_limits(settings)
                        if not limits["trading_allowed"]:
                            sr = limits["stop_reason"]
                            tp = limits["total_pnl"]
                            _add_log("LIMITS_HIT", "", f"{sr}: P&L={tp:.2f}")
                            break

                        closed = await _monitor_open_trades(settings)
                        if closed > 0:
                            limits = await _check_daily_limits(settings)
                            if limits["trading_allowed"]:
                                open_count = await _get_open_trade_count()
                                if open_count < MAX_ACTIVE_TRADES:
                                    _add_log("RESCAN", "",
                                             f"Trade closed, {MAX_ACTIVE_TRADES - open_count} slots, re-scanning")
                                    break
                        await asyncio.sleep(2)
                else:
                    sr = limits["stop_reason"]
                    tp = limits["total_pnl"]
                    _add_log("PAUSED", "", f"{sr}: P&L={tp:.2f}, monitoring only")
                    await _monitor_open_trades(settings)
                    await asyncio.sleep(10)

            elif auto_enabled and not market_open:
                await _monitor_open_trades(settings)
                await asyncio.sleep(5)

            else:
                # Auto-trade disabled: still monitor existing trades
                settings = await _get_settings()
                if settings:
                    await _monitor_open_trades(settings)
                await asyncio.sleep(5)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Auto-trade engine error: {e}")
            _add_log("ERROR", "", f"Engine error: {e}")
            await asyncio.sleep(10)

    _engine_running = False
    logger.info("Auto-trade engine v3 stopped")


# --- Start / Stop / Status ----------------------------------------------------

def start_engine():
    """Start the unified auto-trade engine as a background task."""
    global _engine_task
    if _engine_task is None or _engine_task.done():
        _engine_task = asyncio.create_task(_engine_loop())
    logger.info("Auto-trade engine v3 started")


def stop_engine():
    """Stop the engine background task."""
    global _engine_running
    _engine_running = False
    if _engine_task and not _engine_task.done():
        _engine_task.cancel()
    logger.info("Auto-trade engine v3 stopped")


def get_engine_status() -> dict:
    """Get current engine status."""
    return {
        "engine_running": _engine_running,
        "daily_target_met": _daily_target_met,
        "last_scan_time": _last_scan_time.isoformat() if _last_scan_time else None,
        "last_monitor_time": _last_monitor_time.isoformat() if _last_monitor_time else None,
        "last_reanalysis_time": _last_reanalysis_time.isoformat() if _last_reanalysis_time else None,
        "market_open": is_market_open(),
        "max_active_trades": MAX_ACTIVE_TRADES,
        "signals_count": len(_last_signals),
        "settings_hot_reload": True,
        "trailing_profit_enabled": True,
        "reanalysis_interval_secs": RE_ANALYSIS_INTERVAL_SECS,
        "recent_log": _auto_trade_log[-30:],
    }
