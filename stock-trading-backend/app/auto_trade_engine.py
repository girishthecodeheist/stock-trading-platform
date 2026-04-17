"""Auto Trade Engine v4 - Smarter trading with brokerage awareness.

Improvements over v3:
1. Daily trade limit (max 10/day configurable) + cooldown between trades
2. Brokerage-aware filtering: skip trades where brokerage > expected profit
3. Multi-timeframe analysis: 15m for entry, 1D for trend confirmation
4. Realistic targets: capped to recent price range, tighter ATR multipliers
5. Minimum confidence/score threshold for auto-trades (score >= 25, confidence >= 40)
6. All v3 features retained: capital limits, trailing profit, re-analysis, hot-reload
"""

import json
import math
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

import pandas as pd
from sqlalchemy import text

from app.database import async_session_factory
from app import fyers_client
from app.heatmap_poller import heatmap_poller
from app.indicator_engine import compute_all_indicators
from app.signal_engine import generate_signal
from app.brokerage_calc import (
    calc_brokerage,
    is_trade_profitable_after_brokerage,
    min_qty_for_net_profit,
)

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
MAX_ACTIVE_TRADES = 5

# v4: Trade frequency controls
MAX_TRADES_PER_DAY = 50              # Default cap when ``trading_settings.max_trades_per_day`` is NULL
TRADE_COOLDOWN_SECS = 300            # 5 min cooldown between trades on same symbol
SCAN_INTERVAL_SECS = 120             # Scan every 2 minutes (was 60s)
MIN_SCORE_FOR_TRADE = 25             # Minimum absolute score to place trade
MIN_CONFIDENCE_FOR_TRADE = 40        # Minimum confidence to place trade

# v5: Brokerage-aware sizing floor. Overridden by
# ``trading_settings.min_net_profit_per_trade`` / ``min_profit_to_cost_ratio``.
MIN_NET_PROFIT_PER_TRADE = 1.0       # Rs — skip setups that can't clear this after charges
MIN_PROFIT_TO_COST_RATIO = 1.0       # gross profit must be >= N× total charges (>=1 = any net profit)

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
_trades_placed_today: int = 0
_last_trade_time_per_symbol: Dict[str, datetime] = {}

# SSE subscribers: list of asyncio.Queue objects
_sse_subscribers: list = []

# Last analyzed signals (shared with signals dashboard)
_last_signals: list = []

# Recently-rejected signals (ring buffer). Each scan cycle a signal can be
# dropped at one of several gates (weak score, low confidence, capital limit,
# brokerage filter, cooldown, etc). The UI surfaces this so the user can see
# *why* nothing was placed even though signals looked strong.
_rejected_signals: list = []
REJECTED_SIGNALS_BUFFER = 100

# Cached settings (refreshed on demand, TTL-bounded)
_cached_settings: Optional[dict] = None
_settings_cache_time: float = 0.0
SETTINGS_CACHE_TTL = 10  # seconds — engine tight loops call _get_settings every ~2s

# Cooldown map pruning: anything older than this is removed at the start of
# each scan cycle so the dict can't grow unbounded across a trading session.
COOLDOWN_PRUNE_SECS = 3600  # 1 hour


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


# Reasons we expose to the UI as "blocked" — everything else is either a
# success or an internal error and shouldn't show up on the rejected panel.
_REJECTION_REASONS = {
    "WEAK_SIGNAL",
    "LOW_CONFIDENCE",
    "DAILY_LIMIT",
    "COOLDOWN",
    "TREND_CONFLICT",
    "LIVE_NOT_CONNECTED",
    "CAPITAL_LIMIT",
    "BROKERAGE_FILTER",
    "TRADING_HALTED",
    "OPEN_TRADES_FULL",
    "DUPLICATE_SYMBOL",
    # Broker rejected the order at the Fyers API (e.g. -50 "algo orders
    # not allowed", insufficient funds on the real account, instrument
    # banned for intraday, etc.). Surfaces the raw broker message.
    "FYERS_REJECTED",
}


def _record_rejection(
    reason: str,
    symbol: str,
    signal_data: Optional[dict] = None,
    trade_mode: Optional[str] = None,
    details: str = "",
    extra: Optional[dict] = None,
) -> None:
    """Record a rejected signal for the UI's "Blocked" panel.

    Entries are dedup'd on (symbol, reason) within a 60s window so that a
    stuck gate (e.g. CAPITAL_LIMIT every 15s) shows up as one row that keeps
    refreshing rather than spamming the buffer.
    """
    if reason not in _REJECTION_REASONS:
        return
    global _rejected_signals
    now = datetime.now(IST)
    sig = signal_data or {}
    payload = {
        "time": now.isoformat(),
        "reason": reason,
        "symbol": symbol,
        "trade_mode": trade_mode,
        "details": details,
        "signal": sig.get("signal"),
        "score": sig.get("score"),
        "confidence": sig.get("confidence"),
        "entry_price": sig.get("entry_price"),
        "stop_loss": sig.get("stop_loss"),
        "target": sig.get("target_1") or sig.get("target"),
        "extra": extra or {},
    }
    # Dedup: same symbol+reason within 60s → overwrite the existing row.
    for idx, row in enumerate(_rejected_signals):
        if row.get("symbol") == symbol and row.get("reason") == reason:
            try:
                prev_t = datetime.fromisoformat(row["time"])
            except Exception:
                prev_t = None
            if prev_t and (now - prev_t).total_seconds() < 60:
                _rejected_signals[idx] = payload
                return
    _rejected_signals.append(payload)
    if len(_rejected_signals) > REJECTED_SIGNALS_BUFFER:
        _rejected_signals = _rejected_signals[-REJECTED_SIGNALS_BUFFER:]


def get_recent_rejections(limit: int = 50) -> list:
    """Return the most recent rejections, newest first."""
    return list(reversed(_rejected_signals[-limit:]))


def is_market_open() -> bool:
    """Check if NSE market is currently open."""
    now = datetime.now(IST)
    day = now.weekday()  # 0=Mon, 6=Sun
    if day >= 5:  # Sat/Sun
        return False
    total_mins = now.hour * 60 + now.minute
    return 555 <= total_mins <= 930  # 9:15 AM to 3:30 PM IST


# --- Settings (hot-reload) ---------------------------------------------------

async def _get_settings(force: bool = False) -> Optional[dict]:
    """Get trading settings, cached for SETTINGS_CACHE_TTL seconds.

    The engine's monitor loop calls this every ~2s; without a cache we hit
    the DB ~30 times/minute for data that almost never changes. Pass
    ``force=True`` after a settings mutation to bypass the cache. Settings
    routes also set ``_cached_settings = None`` directly to invalidate.
    """
    global _cached_settings, _settings_cache_time
    now = time.time()
    if (
        not force
        and _cached_settings is not None
        and now - _settings_cache_time < SETTINGS_CACHE_TTL
    ):
        return _cached_settings
    try:
        async with async_session_factory() as db:
            result = await db.execute(text("SELECT * FROM trading_settings WHERE id=1"))
            row = result.mappings().first()
            if row:
                _cached_settings = dict(row)
                _settings_cache_time = now
                return _cached_settings
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")
    return _cached_settings  # Return cached if DB fails


def _cleanup_cooldown_map() -> None:
    """Prune cooldown entries older than COOLDOWN_PRUNE_SECS.

    Called at the start of each scan cycle so the dict can't grow without
    bound across a long trading session.
    """
    now = datetime.now(IST)
    expired = [
        sym
        for sym, ts in _last_trade_time_per_symbol.items()
        if (now - ts).total_seconds() > COOLDOWN_PRUNE_SECS
    ]
    for sym in expired:
        _last_trade_time_per_symbol.pop(sym, None)


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


async def _get_live_available_margin() -> float:
    """Return the broker-reported available margin for LIVE trading.

    Queries Fyers ``funds`` and reads ``equityAmount`` from row id=10
    ("Available Balance"). The Fyers v3 payload exposes balances as
    ``equityAmount`` / ``commodityAmount`` per row — the legacy
    ``limitAmount`` field we previously read is absent in v3, which meant
    every LIVE scan was rejecting with margin=0. We fall back to
    ``limitAmount`` for forward-compat with any v2-style response.

    Returns 0 if we're not authenticated or the call fails — callers treat
    that as "no capacity" and skip the trade.
    """
    if not fyers_client.is_authenticated():
        return 0.0
    try:
        resp = await fyers_client.get_funds_async()
    except Exception as e:
        logger.warning(f"Fyers funds lookup failed: {e}")
        return 0.0
    if not resp or resp.get("s") != "ok":
        logger.warning(f"Fyers funds returned non-ok: {resp}")
        return 0.0
    for row in resp.get("fund_limit", []) or []:
        if row.get("id") == 10:
            raw = row.get("equityAmount")
            if raw is None:
                raw = row.get("limitAmount", 0)
            try:
                return float(raw or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


async def _get_open_trade_count() -> int:
    """Count open trades across both paper_trades and live_trades.

    The engine enforces a single MAX_ACTIVE_TRADES budget regardless of mode
    so that a user who flips from PAPER to LIVE mid-session can't accidentally
    blow past the slot limit.
    """
    async with async_session_factory() as db:
        paper = await db.execute(text("SELECT COUNT(*) FROM paper_trades WHERE status = 'OPEN'"))
        live = await db.execute(text("SELECT COUNT(*) FROM live_trades WHERE status = 'OPEN'"))
        return int(paper.scalar() or 0) + int(live.scalar() or 0)


async def _has_open_trade_for_symbol(symbol: str) -> bool:
    """Check if there is an open trade for ``symbol`` in either mode."""
    async with async_session_factory() as db:
        paper = await db.execute(
            text("SELECT COUNT(*) FROM paper_trades WHERE symbol = :symbol AND status = 'OPEN'"),
            {"symbol": symbol},
        )
        if (paper.scalar() or 0) > 0:
            return True
        live = await db.execute(
            text("SELECT COUNT(*) FROM live_trades WHERE symbol = :symbol AND status = 'OPEN'"),
            {"symbol": symbol},
        )
        return (live.scalar() or 0) > 0


async def _get_trades_placed_today() -> int:
    """Count auto-placed trades today across paper_trades + live_trades.

    Paper trades are flagged via ``is_auto_trade``. Live trades don't have
    that column, so we identify auto-placed ones by ``strategy = 'AUTO'``
    which :func:`_place_auto_trade` writes when in LIVE mode.
    """
    today = datetime.now(IST).date()
    async with async_session_factory() as db:
        paper = await db.execute(text(
            "SELECT COUNT(*) FROM paper_trades "
            "WHERE is_auto_trade = true AND DATE(entry_time) = :today"
        ), {"today": today})
        live = await db.execute(text(
            "SELECT COUNT(*) FROM live_trades "
            "WHERE strategy = 'AUTO' AND DATE(entry_time) = :today"
        ), {"today": today})
        return int(paper.scalar() or 0) + int(live.scalar() or 0)


def _is_on_cooldown(symbol: str) -> bool:
    """Check if symbol is on cooldown (recently traded)."""
    last_time = _last_trade_time_per_symbol.get(symbol)
    if last_time is None:
        return False
    elapsed = (datetime.now(IST) - last_time).total_seconds()
    return elapsed < TRADE_COOLDOWN_SECS


async def _calculate_quantity(
    entry_price: float, sl_pct: float, tgt_pct: float,
    settings: dict, available_margin: float,
    trade_mode: str = "PAPER",
) -> int:
    """Calculate optimal trade quantity based on settings AND available margin.

    Loss/profit caps are mode-aware — previously we only read the PAPER caps
    which meant a user who'd tuned ``day_max_loss_live`` / ``day_profit_target_live``
    had no effect on LIVE auto-trades.

    Capital limit enforcement: quantity * entry_price must not exceed available_margin.
    """
    if str(trade_mode).upper() == "LIVE":
        max_loss = abs(settings.get("day_max_loss_live", 2000) or 0)
        profit_target = settings.get("day_profit_target_live", 4000) or 0
    else:
        max_loss = abs(settings.get("day_max_loss_paper", 1000) or 0)
        profit_target = settings.get("day_profit_target_paper", 2000) or 0

    sl_per_share = entry_price * sl_pct / 100.0
    target_per_share = entry_price * tgt_pct / 100.0

    if sl_per_share <= 0 or target_per_share <= 0:
        if entry_price <= available_margin:
            return 1
        return 0

    qty_from_loss = max_loss / sl_per_share if sl_per_share > 0 and max_loss > 0 else float("inf")
    qty_from_profit = profit_target / target_per_share if target_per_share > 0 and profit_target > 0 else float("inf")
    qty_from_margin = available_margin / entry_price if entry_price > 0 else 0

    optimal_qty = int(math.floor(min(qty_from_loss, qty_from_profit, qty_from_margin)))

    if optimal_qty <= 0:
        logger.info(
            f"Capital limit: qty=0 mode={trade_mode} "
            f"margin={available_margin:.0f} price={entry_price:.2f} "
            f"qty_from_loss={qty_from_loss:.2f} qty_from_profit={qty_from_profit:.2f} "
            f"qty_from_margin={qty_from_margin:.2f}"
        )
        return 0

    return optimal_qty


# --- Recent Price Range (for realistic targets) --------------------------------

async def _get_recent_price_range(symbol: str, days: int = 5) -> Optional[Dict[str, float]]:
    """Get the high/low price range from recent candle data.

    Used to cap targets to realistic levels.
    """
    try:
        if fyers_client.is_authenticated():
            candles = await fyers_client.get_historical_data_async(symbol, timeframe="1D", days_back=days)
            if candles and len(candles) >= 2:
                highs = [float(c["high"]) for c in candles]
                lows = [float(c["low"]) for c in candles]
                closes = [float(c["close"]) for c in candles]
                return {
                    "recent_high": max(highs),
                    "recent_low": min(lows),
                    "avg_range": sum(h - l for h, l in zip(highs, lows)) / len(highs),
                    "avg_close": sum(closes) / len(closes),
                    "last_close": closes[-1],
                }

        async with async_session_factory() as db:
            result = await db.execute(text(
                "SELECT high, low, close FROM ohlcv_candles "
                "WHERE symbol = :symbol AND timeframe = '1D' "
                "ORDER BY timestamp DESC LIMIT :limit"
            ), {"symbol": symbol, "limit": days})
            rows = result.fetchall()
            if rows and len(rows) >= 2:
                highs = [float(r[0]) for r in rows]
                lows = [float(r[1]) for r in rows]
                closes = [float(r[2]) for r in rows]
                return {
                    "recent_high": max(highs),
                    "recent_low": min(lows),
                    "avg_range": sum(h - l for h, l in zip(highs, lows)) / len(highs),
                    "avg_close": sum(closes) / len(closes),
                    "last_close": closes[-1],
                }
    except Exception as e:
        logger.debug(f"Failed to get recent price range for {symbol}: {e}")
    return None


def _cap_targets_to_range(
    entry_price: float, stop_loss: float, target: float,
    side: str, price_range: Optional[Dict[str, float]],
    atr: Optional[float] = None,
) -> tuple:
    """Cap SL and target to realistic levels based on recent price action.

    Returns (adjusted_sl, adjusted_target).
    """
    if price_range is None:
        # No price data - use conservative defaults
        if side == "BUY":
            sl = round(entry_price * 0.99, 2)     # 1% SL
            tgt = round(entry_price * 1.01, 2)    # 1% target
        else:
            sl = round(entry_price * 1.01, 2)
            tgt = round(entry_price * 0.99, 2)
        return sl, tgt

    recent_high = price_range["recent_high"]
    recent_low = price_range["recent_low"]
    avg_range = price_range["avg_range"]

    # Use average daily range as max target distance
    # For intraday: target should be within 50-70% of average daily range
    max_target_distance = avg_range * 0.6

    # ATR-based adjustments (use smaller multiplier for intraday)
    if atr and atr > 0:
        max_target_distance = min(max_target_distance, atr * 0.8)

    if side == "BUY":
        # Cap target to recent high or max_target_distance
        max_target = min(recent_high, entry_price + max_target_distance)
        adjusted_target = min(target, max_target) if target else max_target
        # Ensure target is above entry
        if adjusted_target <= entry_price:
            adjusted_target = round(entry_price * 1.005, 2)  # 0.5% minimum

        # SL: tighter, within 1% or half of target distance
        target_distance = adjusted_target - entry_price
        sl_distance = min(abs(entry_price - stop_loss) if stop_loss else target_distance,
                         target_distance * 0.75,
                         entry_price * 0.01)  # max 1% SL
        adjusted_sl = round(entry_price - sl_distance, 2)
        adjusted_sl = max(adjusted_sl, recent_low)  # Don't set SL below recent low

    else:  # SELL
        # Cap target to recent low or max_target_distance
        min_target = max(recent_low, entry_price - max_target_distance)
        adjusted_target = max(target, min_target) if target else min_target
        # Ensure target is below entry
        if adjusted_target >= entry_price:
            adjusted_target = round(entry_price * 0.995, 2)  # 0.5% minimum

        # SL
        target_distance = entry_price - adjusted_target
        sl_distance = min(abs(stop_loss - entry_price) if stop_loss else target_distance,
                         target_distance * 0.75,
                         entry_price * 0.01)
        adjusted_sl = round(entry_price + sl_distance, 2)
        adjusted_sl = min(adjusted_sl, recent_high)

    return round(adjusted_sl, 2), round(adjusted_target, 2)


# --- Analysis -----------------------------------------------------------------

async def _analyze_stock(stock: dict, timeframe: str = "15m") -> Optional[dict]:
    """Run full indicator + signal engine on a single stock.

    v4: Default timeframe changed from 1D to 15m for intraday.
    Returns signal dict with analysis_basis and analyzed_timeframe, or None.
    """
    symbol = stock["symbol"]
    ltp = stock.get("ltp", 0)
    change_pct = stock.get("change_pct", 0)

    if fyers_client.is_authenticated():
        try:
            prices = await fyers_client.get_live_prices_batch_async([symbol])
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
            candles = await fyers_client.get_historical_data_async(symbol, timeframe=timeframe, days_back=days_back)
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

        # v4: Tighter SL/target for heatmap fallback (was 1.5/2.0)
        sl_pct_val = 0.8
        tgt_pct_val = 1.0
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


# --- Multi-Timeframe Confirmation --------------------------------------------

async def _confirm_with_daily_trend(symbol: str, intraday_signal: str) -> bool:
    """v4: Confirm intraday signal with daily trend.

    If 15m says BUY but 1D trend is bearish, skip the trade.
    Returns True if daily trend confirms the intraday signal.
    """
    if not fyers_client.is_authenticated():
        return True  # Can't confirm, allow the trade

    try:
        candles = await fyers_client.get_historical_data_async(symbol, timeframe="1D", days_back=20)
        if not candles or len(candles) < 10:
            return True  # Not enough data, allow

        df = pd.DataFrame(candles)
        df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float)
        df["volume"] = df["volume"].astype(float)
        indicators = compute_all_indicators(df)
        daily_signal = generate_signal(indicators, instrument_type="EQUITY")

        daily_direction = daily_signal.get("signal", "NEUTRAL")
        daily_score = daily_signal.get("score", 0)

        # Check for conflict
        if "BUY" in intraday_signal.upper():
            # Intraday wants to BUY - check daily isn't strongly bearish
            if "SELL" in daily_direction.upper() and daily_score <= -30:
                _add_log("TREND_CONFLICT", symbol,
                         f"Intraday={intraday_signal} but Daily={daily_direction} (score={daily_score}). Skipping.")
                return False
        elif "SELL" in intraday_signal.upper():
            # Intraday wants to SELL - check daily isn't strongly bullish
            if "BUY" in daily_direction.upper() and daily_score >= 30:
                _add_log("TREND_CONFLICT", symbol,
                         f"Intraday={intraday_signal} but Daily={daily_direction} (score={daily_score}). Skipping.")
                return False

        return True
    except Exception as e:
        logger.debug(f"Daily trend confirmation failed for {symbol}: {e}")
        return True  # On error, allow the trade


# --- Trade Placement ----------------------------------------------------------

async def _place_auto_trade(
    symbol: str, signal_data: dict, settings: dict,
    analysis_basis: str = "technical", analyzed_timeframe: str = "15m"
) -> Optional[int]:
    """Auto-place a paper trade based on signal. Returns trade_id or None.

    v4 additions:
    - Daily trade limit check
    - Cooldown check per symbol
    - Brokerage profitability filter
    - Realistic target capping
    - Multi-timeframe trend confirmation
    - Minimum score/confidence threshold
    """
    global _trades_placed_today
    try:
        signal_type = signal_data.get("signal", "NEUTRAL")
        entry_price = signal_data.get("entry_price", 0)
        stop_loss = signal_data.get("stop_loss")
        target = signal_data.get("target_1") or signal_data.get("target")
        confidence = signal_data.get("confidence", 0)
        score = signal_data.get("score", 0)
        reasons = signal_data.get("reasons", [])
        atr = signal_data.get("atr")

        if not entry_price or entry_price <= 0:
            return None

        if "BUY" in signal_type.upper():
            side = "BUY"
        elif "SELL" in signal_type.upper():
            side = "SELL"
        else:
            return None

        # v4: MINIMUM SCORE/CONFIDENCE THRESHOLD
        max_trades_day = settings.get("max_trades_per_day", MAX_TRADES_PER_DAY)
        min_score = settings.get("min_score_for_trade", MIN_SCORE_FOR_TRADE)
        min_confidence = settings.get("min_confidence_for_trade", MIN_CONFIDENCE_FOR_TRADE)

        trade_mode_peek = str(settings.get("trade_mode") or "PAPER").upper()

        if abs(score) < min_score:
            _add_log("WEAK_SIGNAL", symbol,
                     f"Score {score:.1f} below threshold {min_score}. Skipping.")
            _record_rejection(
                "WEAK_SIGNAL", symbol, signal_data, trade_mode_peek,
                f"Score {score:.1f} < min {min_score}",
            )
            return None

        if confidence < min_confidence:
            _add_log("LOW_CONFIDENCE", symbol,
                     f"Confidence {confidence:.1f} below threshold {min_confidence}. Skipping.")
            _record_rejection(
                "LOW_CONFIDENCE", symbol, signal_data, trade_mode_peek,
                f"Confidence {confidence:.1f} < min {min_confidence}",
            )
            return None

        # v4: DAILY TRADE LIMIT
        _trades_placed_today = await _get_trades_placed_today()
        if _trades_placed_today >= max_trades_day:
            _add_log("DAILY_LIMIT", symbol,
                     f"Daily trade limit ({max_trades_day}) reached. {_trades_placed_today} trades today.")
            _push_event("DAILY_TRADE_LIMIT", {
                "symbol": symbol,
                "trades_today": _trades_placed_today,
                "max_trades": max_trades_day,
            })
            _record_rejection(
                "DAILY_LIMIT", symbol, signal_data, trade_mode_peek,
                f"{_trades_placed_today}/{max_trades_day} trades placed today",
            )
            return None

        # v4: COOLDOWN CHECK
        if _is_on_cooldown(symbol):
            last_time = _last_trade_time_per_symbol.get(symbol)
            elapsed = int((datetime.now(IST) - last_time).total_seconds()) if last_time else 0
            _add_log("COOLDOWN", symbol,
                     f"On cooldown ({elapsed}s / {TRADE_COOLDOWN_SECS}s). Skipping.")
            _record_rejection(
                "COOLDOWN", symbol, signal_data, trade_mode_peek,
                f"Re-entry cooldown: {elapsed}s / {TRADE_COOLDOWN_SECS}s",
            )
            return None

        # v4: MULTI-TIMEFRAME CONFIRMATION
        if analyzed_timeframe != "1D":
            confirmed = await _confirm_with_daily_trend(symbol, signal_type)
            if not confirmed:
                _record_rejection(
                    "TREND_CONFLICT", symbol, signal_data, trade_mode_peek,
                    f"{analyzed_timeframe} {signal_type} conflicts with 1D trend",
                )
                return None

        # v4: REALISTIC TARGET CAPPING
        price_range = await _get_recent_price_range(symbol, days=5)
        if stop_loss and target:
            stop_loss, target = _cap_targets_to_range(
                entry_price, stop_loss, target, side, price_range, atr
            )

        sl_pct = settings.get("default_sl_percent", 1.0)
        tgt_pct = settings.get("default_target_percent", 1.0)

        if stop_loss and entry_price:
            sl_pct = abs((entry_price - stop_loss) / entry_price * 100)
        if target and entry_price:
            tgt_pct = abs((target - entry_price) / entry_price * 100)

        # MODE ROUTING: PAPER writes to paper_trades; LIVE places a real Fyers
        # order and writes to live_trades. Mode is read per-call from settings
        # so a mid-session flip is picked up on the next scan.
        trade_mode = str(settings.get("trade_mode") or "PAPER").upper()
        if trade_mode not in ("PAPER", "LIVE"):
            trade_mode = "PAPER"

        if trade_mode == "LIVE" and not fyers_client.is_authenticated():
            _add_log("LIVE_NOT_CONNECTED", symbol,
                     "LIVE mode selected but Fyers is not authenticated. Skipping.")
            _push_event("LIVE_NOT_CONNECTED", {
                "symbol": symbol,
                "message": "Fyers not connected; connect to place live trades.",
            })
            _record_rejection(
                "LIVE_NOT_CONNECTED", symbol, signal_data, trade_mode,
                "Fyers not authenticated — connect in the UI to place live trades.",
            )
            return None

        # CAPITAL LIMIT ENFORCEMENT
        if trade_mode == "LIVE":
            available_margin = await _get_live_available_margin()
        else:
            available_margin = await _get_available_margin(settings)
        quantity = await _calculate_quantity(
            entry_price, sl_pct, tgt_pct, settings, available_margin, trade_mode,
        )

        if quantity <= 0:
            _add_log("CAPITAL_LIMIT", symbol,
                     f"Rejected: mode={trade_mode}, margin={available_margin:.0f}, "
                     f"price={entry_price:.2f}")
            _push_event("CAPITAL_LIMIT", {
                "symbol": symbol,
                "mode": trade_mode,
                "available_margin": round(available_margin, 2),
                "required": round(entry_price, 2),
                "message": "Insufficient capital to place trade"
            })
            _record_rejection(
                "CAPITAL_LIMIT", symbol, signal_data, trade_mode,
                f"Available {trade_mode} margin \u20b9{available_margin:.0f} insufficient "
                f"for 1 share @ \u20b9{entry_price:.2f}",
                extra={
                    "available_margin": round(available_margin, 2),
                    "required": round(entry_price, 2),
                },
            )
            return None

        trade_cost = entry_price * quantity
        if trade_cost > available_margin:
            quantity = int(math.floor(available_margin / entry_price))
            if quantity <= 0:
                _add_log("CAPITAL_LIMIT", symbol,
                         f"Rejected after margin check: mode={trade_mode}, "
                         f"margin={available_margin:.0f}")
                _record_rejection(
                    "CAPITAL_LIMIT", symbol, signal_data, trade_mode,
                    f"Trade cost exceeds {trade_mode} margin \u20b9{available_margin:.0f}",
                    extra={"available_margin": round(available_margin, 2)},
                )
                return None
            trade_cost = entry_price * quantity

        # v5: BROKERAGE-AWARE SIZING + PROFITABILITY GATE
        # Picking 1 share of a low-priced stock can mean charges eat the whole
        # move. If the user has ``auto_quantity_enabled`` on, bump qty up to
        # the smallest value that clears the configured net-profit floor
        # (capped by loss/margin budgets); otherwise apply the gate as a filter.
        if target and entry_price and target != entry_price:
            min_net = float(
                settings.get("min_net_profit_per_trade", MIN_NET_PROFIT_PER_TRADE) or 0
            )
            min_ratio = float(
                settings.get("min_profit_to_cost_ratio", MIN_PROFIT_TO_COST_RATIO) or 0
            )

            auto_qty = bool(settings.get("auto_quantity_enabled", True))
            if auto_qty and min_net > 0:
                # Upper bound: whichever is tighter of margin or loss cap.
                max_loss = abs(settings.get("day_max_loss_paper", 1000) or 0) if trade_mode == "PAPER" \
                    else abs(settings.get("day_max_loss_live", 2000) or 0)
                sl_per_share = entry_price * sl_pct / 100.0 if sl_pct > 0 else entry_price
                qty_from_loss = int(math.floor(max_loss / sl_per_share)) if sl_per_share > 0 else 0
                qty_from_margin = int(math.floor(available_margin / entry_price)) if entry_price > 0 else 0
                max_qty_cap = min(q for q in (qty_from_loss, qty_from_margin) if q > 0) \
                    if (qty_from_loss > 0 and qty_from_margin > 0) \
                    else max(qty_from_loss, qty_from_margin, 1)

                bumped = min_qty_for_net_profit(
                    entry_price, target,
                    min_net_profit=min_net,
                    min_profit_ratio=min_ratio,
                    max_qty=max(quantity, max_qty_cap),
                )
                if bumped > quantity and bumped <= max_qty_cap:
                    _add_log("QTY_BUMP", symbol,
                             f"qty {quantity}→{bumped} to clear net profit "
                             f"floor ₹{min_net:.0f} after charges")
                    quantity = bumped
                    trade_cost = entry_price * quantity

            brokerage_check = is_trade_profitable_after_brokerage(
                entry_price, target, quantity,
                min_profit_ratio=min_ratio,
                min_net_profit=min_net,
            )
            if not brokerage_check["profitable"]:
                reason = []
                if not brokerage_check.get("ratio_ok", True):
                    reason.append(f"ratio {brokerage_check['profit_to_cost_ratio']:.2f}<{min_ratio}")
                if not brokerage_check.get("net_ok", True):
                    reason.append(f"net ₹{brokerage_check['net_profit']:.2f}<₹{min_net:.0f}")
                _add_log("BROKERAGE_FILTER", symbol,
                         f"Rejected: gross=₹{brokerage_check['gross_profit']:.2f}, "
                         f"charges=₹{brokerage_check['total_charges']:.2f}, "
                         f"qty={quantity} ({', '.join(reason) or 'n/a'})")
                _push_event("BROKERAGE_FILTER", {
                    "symbol": symbol,
                    "qty": quantity,
                    "gross_profit": brokerage_check["gross_profit"],
                    "total_charges": brokerage_check["total_charges"],
                    "net_profit": brokerage_check["net_profit"],
                    "min_net_profit": min_net,
                    "min_required_ratio": min_ratio,
                    "message": "Trade rejected: would not be profitable after charges"
                })
                _record_rejection(
                    "BROKERAGE_FILTER", symbol, signal_data, trade_mode,
                    f"Net \u20b9{brokerage_check['net_profit']:.2f} at qty={quantity} "
                    f"(gross \u20b9{brokerage_check['gross_profit']:.2f} \u2212 charges "
                    f"\u20b9{brokerage_check['total_charges']:.2f}); "
                    f"{', '.join(reason) or 'below floor'}",
                    extra={
                        "qty": quantity,
                        "gross_profit": round(brokerage_check["gross_profit"], 2),
                        "total_charges": round(brokerage_check["total_charges"], 2),
                        "net_profit": round(brokerage_check["net_profit"], 2),
                        "min_net_profit": min_net,
                        "min_profit_to_cost_ratio": min_ratio,
                        "profit_to_cost_ratio": round(
                            brokerage_check.get("profit_to_cost_ratio") or 0, 3
                        ),
                        "charges_breakdown": brokerage_check.get("charges_breakdown"),
                    },
                )
                return None

        snapshot = {
            "auto_trade": True,
            "mode": trade_mode,
            "signal_type": signal_type,
            "score": score,
            "analysis_basis": analysis_basis,
            "analyzed_timeframe": analyzed_timeframe,
            "available_margin_at_entry": round(available_margin, 2),
            "trade_cost": round(trade_cost, 2),
        }

        full_reasons = list(reasons) if isinstance(reasons, list) else [str(reasons)]
        full_reasons.insert(0, f"[{analyzed_timeframe}] {analysis_basis}")

        if trade_mode == "LIVE":
            # Place the real entry order on Fyers FIRST — only persist the
            # trade if the broker accepts it, otherwise we'd have a phantom
            # OPEN row that monitor loops would try to close via a non-existent
            # position.
            fyers_side = 1 if side == "BUY" else -1
            product_type = str(settings.get("product_type") or "INTRADAY").upper()
            entry_resp = await fyers_client.place_order_async(
                symbol=symbol,
                side=fyers_side,
                qty=quantity,
                order_type=2,  # MARKET
                product_type=product_type,
            )
            logger.info(f"Fyers auto entry response for {symbol}: {entry_resp}")
            if not entry_resp or entry_resp.get("s") != "ok":
                err = entry_resp.get("message", "Unknown") if entry_resp else "No response"
                code = entry_resp.get("code") if entry_resp else None
                # Fyers returns -50 "Algo orders are not allowed from this
                # app <APP_ID>" when the app hasn't been whitelisted for
                # API/algo trading. It's an app-level Fyers setting — the
                # user has to request enablement from Fyers (myaccount →
                # My APIs → Algo). Surface that as actionable text, not
                # just the raw broker message.
                hint = ""
                if code == -50 or "algo orders are not allowed" in (err or "").lower():
                    hint = (
                        " — Enable API/Algo trading for this app in Fyers "
                        "(myaccount.fyers.in → My APIs → request algo "
                        "activation). Orders will keep failing until that's on."
                    )
                _add_log("LIVE_ORDER_FAILED", symbol, f"Fyers rejected: {err}{hint}")
                _push_event("LIVE_ORDER_FAILED", {
                    "symbol": symbol, "side": side, "qty": quantity,
                    "error": err, "code": code,
                    "hint": hint.strip(" —") or None,
                })
                # Surface broker rejection in the "Blocked signals" panel so
                # the user sees *why* LIVE orders aren't going through.
                _record_rejection(
                    "FYERS_REJECTED",
                    symbol,
                    signal_data={
                        "signal": signal_type,
                        "score": score,
                        "confidence": confidence,
                        "entry_price": entry_price,
                        "stop_loss": stop_loss,
                        "target_1": target,
                    },
                    trade_mode=trade_mode,
                    details=f"Fyers: {err}{hint}",
                    extra={
                        "code": code,
                        "qty": quantity,
                        "side": side,
                        "hint": hint.strip(" —") or None,
                    },
                )
                return None
            fyers_order_id = entry_resp.get("id", "") or ""

            # Post-placement reconciliation (F4). Ask Fyers how much
            # actually filled before we create the live_trades row. This
            # protects against partial fills (where we'd otherwise try to
            # exit more shares than we own) and post-ack rejects.
            fill_info = await fyers_client.reconcile_order_async(
                fyers_order_id, timeout_seconds=15.0, poll_interval_seconds=0.5
            )
            logger.info(f"Fyers auto fill reconciliation for {symbol}: {fill_info}")
            broker_status = str(fill_info.get("status") or "PENDING")
            filled_qty = int(fill_info.get("filled_qty") or 0)
            avg_fill = float(fill_info.get("avg_price") or 0.0) or None

            if broker_status in ("REJECTED", "CANCELLED") or (
                broker_status == "UNKNOWN" and filled_qty == 0
            ):
                err_msg = fill_info.get("message") or broker_status.lower()
                _add_log(
                    "LIVE_ORDER_NO_FILL",
                    symbol,
                    f"Fyers {broker_status.lower()}: {err_msg}",
                )
                _record_rejection(
                    "FYERS_REJECTED",
                    symbol,
                    signal_data={
                        "signal": signal_type,
                        "score": score,
                        "confidence": confidence,
                        "entry_price": entry_price,
                        "stop_loss": stop_loss,
                        "target_1": target,
                    },
                    trade_mode=trade_mode,
                    details=f"Broker {broker_status}: {err_msg}",
                    extra={
                        "qty": quantity,
                        "side": side,
                        "broker_status": broker_status,
                        "order_id": fyers_order_id,
                    },
                )
                return None

            if filled_qty == 0:
                broker_status = "PENDING"

            effective_qty = filled_qty if filled_qty > 0 else quantity
            effective_entry = avg_fill if avg_fill else entry_price

            direction = "LONG" if side == "BUY" else "SHORT"
            display_sym = symbol.replace("NSE:", "").replace("-EQ", "")
            risk = abs(effective_entry - stop_loss) if stop_loss else 0
            reward = abs(target - effective_entry) if target else 0
            rr = round(reward / risk, 2) if risk > 0 else 0

            now_ist = datetime.now(IST)
            async with async_session_factory() as db:
                count_result = await db.execute(text(
                    "SELECT COUNT(*) FROM live_trades WHERE DATE(created_at) = :today"
                ), {"today": now_ist.date()})
                count = int(count_result.scalar() or 0)
                trade_ref = f"LT-{now_ist.strftime('%Y%m%d')}-{count + 1:04d}"

                result = await db.execute(text(
                    "INSERT INTO live_trades "
                    "(trade_ref, symbol, display_symbol, direction, entry_price, "
                    " entry_time, quantity, filled_quantity, avg_fill_price, "
                    " broker_status, product_type, stop_loss, sl_percent, "
                    " target_price, target_percent, strategy, risk_reward, "
                    " signal_score, signal_strength, status, fyers_order_id) "
                    "VALUES (:trade_ref, :symbol, :display_sym, :direction, :entry_price, "
                    " :entry_time, :quantity, :filled_qty, :avg_fill, "
                    " :broker_status, :product_type, :stop_loss, :sl_pct, "
                    " :target_price, :tgt_pct, 'AUTO', :rr, "
                    " :signal_score, :signal_strength, 'OPEN', :order_id) "
                    "RETURNING id"
                ), {
                    "trade_ref": trade_ref,
                    "symbol": symbol,
                    "display_sym": display_sym,
                    "direction": direction,
                    "entry_price": effective_entry,
                    "entry_time": now_ist.replace(tzinfo=None),
                    "quantity": effective_qty,
                    "filled_qty": filled_qty,
                    "avg_fill": avg_fill,
                    "broker_status": broker_status,
                    "product_type": product_type,
                    "stop_loss": stop_loss,
                    "sl_pct": round(sl_pct, 2),
                    "target_price": target,
                    "tgt_pct": round(tgt_pct, 2),
                    "rr": rr,
                    "signal_score": score,
                    "signal_strength": signal_type,
                    "order_id": fyers_order_id,
                })
                await db.commit()
                trade_id = result.scalar()
                if broker_status == "PARTIAL":
                    _add_log(
                        "LIVE_PARTIAL_FILL",
                        symbol,
                        f"Partial fill: {filled_qty}/{quantity} @ ₹{(avg_fill or entry_price):.2f}",
                    )
            # Log/SSE downstream must reflect what actually filled, not what
            # we requested. PAPER mode trivially uses the original values.
            quantity = effective_qty
            entry_price = effective_entry
        else:
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

        # Update cooldown tracking
        _last_trade_time_per_symbol[symbol] = datetime.now(IST)
        _trades_placed_today += 1

        _add_log("AUTO_PLACE", symbol,
                 f"[{trade_mode}] Trade #{trade_id}: {side} {quantity}x @ {entry_price}, "
                 f"SL={stop_loss}, Target={target}, Signal={signal_type}, "
                 f"Basis={analysis_basis}, TF={analyzed_timeframe}, "
                 f"Cost={trade_cost:.0f}, Margin={available_margin:.0f}, "
                 f"Trades today: {_trades_placed_today}/{max_trades_day}")
        logger.info(f"Auto-placed [{trade_mode}] trade #{trade_id}: {side} {symbol} "
                     f"{quantity}x @ {entry_price} [{analyzed_timeframe}/{analysis_basis}] "
                     f"(SL={stop_loss}, Tgt={target}, Score={score}, Conf={confidence})")

        _push_event("TRADE_PLACED", {
            "trade_id": trade_id,
            "symbol": symbol,
            "mode": trade_mode,
            "side": side,
            "quantity": quantity,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "target": target,
            "signal": signal_type,
            "score": score,
            "analysis_basis": analysis_basis,
            "analyzed_timeframe": analyzed_timeframe,
            "trades_today": _trades_placed_today,
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
    """Main scan: get top 20 from heatmap, analyze, trade top picks.

    v4: Uses 15m timeframe, applies all filters before placing.
    """
    global _last_scan_time, _last_signals

    _cleanup_cooldown_map()

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

    # v4: Check daily trade limit. If hit, we still scan & publish signals so
    # the dashboard stays informative — we just skip the placement phase.
    max_trades_day = settings.get("max_trades_per_day", MAX_TRADES_PER_DAY)
    trades_today = await _get_trades_placed_today()
    daily_cap_hit = trades_today >= max_trades_day
    if daily_cap_hit:
        _add_log("DAILY_LIMIT", "",
                 f"Daily trade limit reached ({trades_today}/{max_trades_day}) "
                 f"— computing signals for display only")

    top20 = await _get_top20_stocks()
    if not top20:
        logger.info("Auto-trade: No stocks in heatmap")
        _last_scan_time = datetime.now(IST)
        return 0

    _add_log("SCAN_START", "", f"Scanning top {len(top20)} stocks (10 gainers + 10 losers)")

    # v4: Use 15m timeframe for intraday analysis
    analyzed: list = []
    for stock in top20:
        result = await _analyze_stock(stock, timeframe="15m")
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
            "analyzed_timeframe": item.get("analyzed_timeframe", "15m"),
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
        and abs(a["signal_data"].get("score", 0)) >= MIN_SCORE_FOR_TRADE
        and a["signal_data"].get("confidence", 0) >= MIN_CONFIDENCE_FOR_TRADE
    ]
    tradeable.sort(key=lambda x: abs(x["signal_data"].get("score", 0)), reverse=True)

    placed = 0
    for pick in tradeable[:slots]:
        sym = pick["symbol"]
        if await _has_open_trade_for_symbol(sym):
            continue
        if _is_on_cooldown(sym):
            continue
        trade_id = await _place_auto_trade(
            sym, pick["signal_data"], settings,
            analysis_basis=pick.get("analysis_basis", "technical"),
            analyzed_timeframe=pick.get("analyzed_timeframe", "15m")
        )
        if trade_id:
            placed += 1
            if placed >= slots:
                break

    _last_scan_time = datetime.now(IST)
    _add_log("SCAN_COMPLETE", "",
             f"Analyzed {len(analyzed)}/{len(top20)}, placed {placed} trades "
             f"(slots: {slots}, trades today: {_trades_placed_today}/{max_trades_day})")
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
            prices = await fyers_client.get_live_prices_batch_async(batch)
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
        _add_log("MONITOR", "", f"Auto-closed {closed_count} paper trades")

    # Live trades live in a separate table and must be closed via a real Fyers
    # exit order; delegate to a dedicated monitor so PAPER and LIVE bookkeeping
    # stay cleanly separated.
    try:
        live_closed = await _monitor_live_open_trades()
    except Exception as e:
        logger.error(f"Live-trade monitor error: {e}")
        live_closed = 0

    return closed_count + live_closed


async def _monitor_live_open_trades() -> int:
    """Close live trades whose SL/target has been hit.

    Mirrors the paper-trade monitor but operates on ``live_trades`` rows and
    routes the exit through ``fyers_client.place_order_async``. We intentionally
    keep this conservative — no trailing/re-analysis for live — until that
    behaviour has been validated end-to-end on a real account.
    """
    if not fyers_client.is_authenticated():
        return 0

    async with async_session_factory() as db:
        result = await db.execute(text(
            "SELECT id, trade_ref, symbol, direction, entry_price, quantity, "
            "       stop_loss, target_price, entry_time, filled_quantity, "
            "       avg_fill_price, product_type "
            "FROM live_trades WHERE status = 'OPEN'"
        ))
        open_trades = result.fetchall()

    if not open_trades:
        return 0

    symbols = list({t[2] for t in open_trades})
    prices: dict = {}
    for i in range(0, len(symbols), 50):
        batch = symbols[i:i + 50]
        try:
            prices.update(await fyers_client.get_live_prices_batch_async(batch))
        except Exception as e:
            logger.error(f"Live monitor: price fetch failed: {e}")

    if not prices:
        return 0

    closed = 0
    for row in open_trades:
        trade_id = row[0]
        trade_ref = row[1]
        symbol = row[2]
        direction = row[3]  # LONG / SHORT
        # Exit P&L / order qty must use the actual filled entry state,
        # not the row's original request. ``entry_price`` and ``quantity``
        # columns are rewritten to reflect the fill at create time, so
        # they're safe; but we prefer the explicit columns when present
        # (older rows won't have them).
        entry_price = float(row[10]) if row[10] is not None else float(row[4])
        filled_qty = int(row[9]) if row[9] is not None else int(row[5] or 0)
        quantity = filled_qty if filled_qty > 0 else int(row[5] or 0)
        stop_loss = float(row[6]) if row[6] is not None else None
        target_price = float(row[7]) if row[7] is not None else None
        entry_time = row[8]
        product_type = str(row[11] or "INTRADAY").upper()

        price_data = prices.get(symbol) or {}
        ltp = float(price_data.get("ltp", 0) or 0)
        if ltp <= 0 or quantity <= 0:
            continue

        exit_reason = None
        exit_price = ltp
        if direction == "LONG":
            if stop_loss is not None and ltp <= stop_loss:
                exit_reason, exit_price = "AUTO_SL_HIT", stop_loss
            elif target_price is not None and ltp >= target_price:
                exit_reason, exit_price = "AUTO_TARGET_HIT", target_price
        else:  # SHORT
            if stop_loss is not None and ltp >= stop_loss:
                exit_reason, exit_price = "AUTO_SL_HIT", stop_loss
            elif target_price is not None and ltp <= target_price:
                exit_reason, exit_price = "AUTO_TARGET_HIT", target_price

        if not exit_reason:
            continue

        if quantity <= 0:
            # Defensive: nothing actually filled on entry. Just flip to
            # CLOSED locally; no exit order to place.
            async with async_session_factory() as db:
                await db.execute(text(
                    "UPDATE live_trades SET status='CLOSED', "
                    "exit_time=:t, exit_reason='NO_FILL' WHERE id=:id"
                ), {"id": trade_id, "t": datetime.now(IST).replace(tzinfo=None)})
                await db.commit()
            continue

        exit_side = -1 if direction == "LONG" else 1
        exit_resp = await fyers_client.place_order_async(
            symbol=symbol,
            side=exit_side,
            qty=quantity,
            order_type=2,  # MARKET
            product_type=product_type,
        )
        logger.info(
            f"Fyers auto exit response for {trade_ref or trade_id} "
            f"(qty={quantity}, product={product_type}): {exit_resp}"
        )
        if not exit_resp or exit_resp.get("s") != "ok":
            err = exit_resp.get("message", "Unknown") if exit_resp else "No response"
            _add_log("LIVE_EXIT_FAILED", symbol,
                     f"Trade #{trade_id}: Fyers rejected exit: {err}")
            continue

        fyers_exit_id = exit_resp.get("id", "") or ""

        # Reconcile the exit order so P&L uses the real fill price /
        # quantity (may be partial if liquidity was thin).
        exit_fill = await fyers_client.reconcile_order_async(
            fyers_exit_id, timeout_seconds=10.0, poll_interval_seconds=0.5
        )
        if exit_fill.get("status") == "FILLED" and exit_fill.get("avg_price"):
            exit_price = float(exit_fill["avg_price"])
        if exit_fill.get("filled_qty"):
            quantity = int(exit_fill["filled_qty"])

        if direction == "LONG":
            gross_pnl = (exit_price - entry_price) * quantity
        else:
            gross_pnl = (entry_price - exit_price) * quantity

        trade_value = exit_price * quantity
        charges = calc_brokerage(trade_value, quantity)
        net_pnl = gross_pnl - charges["total_charges"]
        now = datetime.now(IST)
        duration = int((now - entry_time).total_seconds() / 60) if entry_time else 0

        async with async_session_factory() as db:
            await db.execute(text("""
                UPDATE live_trades SET
                    status = 'CLOSED',
                    exit_price = :exit_price,
                    exit_time = :exit_time,
                    exit_reason = :exit_reason,
                    gross_pnl = :gross_pnl,
                    brokerage = :brokerage,
                    stt = :stt,
                    exchange_charges = :exchange_charges,
                    gst = :gst,
                    sebi_charges = :sebi_charges,
                    stamp_duty = :stamp_duty,
                    net_pnl = :net_pnl,
                    trade_duration_minutes = :duration,
                    fyers_target_order_id = COALESCE(fyers_target_order_id, :exit_id)
                WHERE id = :id AND status = 'OPEN'
            """), {
                "exit_price": round(exit_price, 2),
                "exit_time": now.replace(tzinfo=None),
                "exit_reason": exit_reason,
                "gross_pnl": round(gross_pnl, 2),
                "brokerage": charges["brokerage"],
                "stt": charges["stt"],
                "exchange_charges": charges["exchange_charges"],
                "gst": charges["gst"],
                "sebi_charges": charges["sebi_charges"],
                "stamp_duty": charges["stamp_duty"],
                "net_pnl": round(net_pnl, 2),
                "duration": duration,
                "exit_id": fyers_exit_id,
                "id": trade_id,
            })
            await db.commit()

        closed += 1
        _add_log("AUTO_CLOSE", symbol,
                 f"[LIVE] Trade #{trade_id}: {exit_reason}, Exit={exit_price}, "
                 f"Net P&L={net_pnl:+.2f}")
        logger.info(f"Auto-closed [LIVE] trade #{trade_id}: {exit_reason} @ {exit_price}, "
                     f"gross={gross_pnl:+.2f}, net={net_pnl:+.2f}")

        _push_event("TRADE_CLOSED", {
            "trade_id": trade_id,
            "mode": "LIVE",
            "symbol": symbol,
            "side": "BUY" if direction == "LONG" else "SELL",
            "exit_price": round(exit_price, 2),
            "exit_reason": exit_reason,
            "gross_pnl": round(gross_pnl, 2),
            "net_pnl": round(net_pnl, 2),
        })

    if closed:
        _add_log("MONITOR", "", f"Auto-closed {closed} live trades")
    return closed


# --- Engine Loop (HOT-RELOAD settings every cycle) ----------------------------

async def _engine_loop():
    """Unified engine loop with HOT-RELOAD settings every cycle.

    v4: Scan interval increased to 120s (was 60s) to reduce trade frequency.
    """
    global _engine_running, _daily_target_met, _trades_placed_today
    _engine_running = True
    _daily_target_met = False
    _trades_placed_today = 0
    logger.info("Auto-trade engine v4 started (smart trading, brokerage-aware)")
    _add_log("ENGINE", "",
             "Auto-trade engine v4 started (brokerage-aware, multi-TF, "
             f"max {MAX_TRADES_PER_DAY}/day, {SCAN_INTERVAL_SECS}s scan interval)")

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

                    # v4: Monitor loop with longer scan interval (120s instead of 60s)
                    monitor_iterations = SCAN_INTERVAL_SECS // 2  # 60 iterations * 2s = 120s
                    for _ in range(monitor_iterations):
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
                                    # v4: Still check daily trade limit before re-scanning
                                    trades_today = await _get_trades_placed_today()
                                    max_trades_day = settings.get("max_trades_per_day", MAX_TRADES_PER_DAY)
                                    if trades_today < max_trades_day:
                                        _add_log("RESCAN", "",
                                                 f"Trade closed, {MAX_ACTIVE_TRADES - open_count} slots, "
                                                 f"{trades_today}/{max_trades_day} trades today, re-scanning")
                                        break
                                    else:
                                        _add_log("DAILY_LIMIT", "",
                                                 f"Trade closed but daily limit reached ({trades_today}/{max_trades_day})")
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
    logger.info("Auto-trade engine v4 stopped")


# --- Start / Stop / Status ----------------------------------------------------

def start_engine():
    """Start the unified auto-trade engine as a background task."""
    global _engine_task
    if _engine_task is None or _engine_task.done():
        _engine_task = asyncio.create_task(_engine_loop())
    logger.info("Auto-trade engine v4 started")


def stop_engine():
    """Stop the engine background task."""
    global _engine_running
    _engine_running = False
    if _engine_task and not _engine_task.done():
        _engine_task.cancel()
    logger.info("Auto-trade engine v4 stopped")


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
        "max_trades_per_day": (
            (_cached_settings or {}).get("max_trades_per_day") or MAX_TRADES_PER_DAY
        ),
        "trades_placed_today": _trades_placed_today,
        "signals_count": len(_last_signals),
        "settings_hot_reload": True,
        "trailing_profit_enabled": True,
        "reanalysis_interval_secs": RE_ANALYSIS_INTERVAL_SECS,
        "scan_interval_secs": SCAN_INTERVAL_SECS,
        "min_score_for_trade": MIN_SCORE_FOR_TRADE,
        "min_confidence_for_trade": MIN_CONFIDENCE_FOR_TRADE,
        "trade_cooldown_secs": TRADE_COOLDOWN_SECS,
        "brokerage_aware": True,
        "multi_timeframe": True,
        "recent_log": _auto_trade_log[-30:],
    }
