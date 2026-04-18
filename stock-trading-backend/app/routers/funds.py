"""Funds API endpoints - Paper and Live fund management."""

import logging
import time
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app import fyers_client
from app.day_limits_engine import day_limits_engine
from app.brokerage_calc import estimate_round_trip_cost

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/funds", tags=["Funds"])

IST = timezone(timedelta(hours=5, minutes=30))

# Short-lived cache for /combined — the dashboard polls this every few seconds
# from multiple components. 5s TTL collapses bursts into a single DB + Fyers
# round trip while still feeling real-time to the UI.
_combined_funds_cache: dict = {"data": None, "timestamp": 0.0}
COMBINED_FUNDS_CACHE_TTL = 5  # seconds


@router.get("/paper")
async def get_paper_funds(db: AsyncSession = Depends(get_db)):
    """Get paper (simulated) fund summary."""
    settings_result = await db.execute(text("SELECT * FROM trading_settings WHERE id=1"))
    settings = settings_result.mappings().first()
    if not settings:
        return {"success": False, "error": "Settings not initialized"}

    capital = float(settings["simulated_capital"])
    today = datetime.now(IST).date()

    # Open trades exposure
    open_result = await db.execute(text("""
        SELECT COALESCE(SUM(entry_price * quantity), 0) as exposure,
               COUNT(*) as open_count
        FROM paper_trades WHERE status = 'OPEN'
    """))
    open_row = open_result.mappings().first()
    open_exposure = float(open_row["exposure"]) if open_row else 0
    open_count = int(open_row["open_count"]) if open_row else 0

    # Realized P&L — both gross (legacy ``pnl_amount``) and net after the
    # Fyers charge breakdown (F3). Old rows with NULL net_pnl fall back to
    # pnl_amount so total_pnl remains meaningful on historical data.
    total_pnl_result = await db.execute(text("""
        SELECT COALESCE(SUM(pnl_amount), 0)                    AS total_gross,
               COALESCE(SUM(COALESCE(net_pnl, pnl_amount)), 0) AS total_net,
               COALESCE(SUM(COALESCE(brokerage, 0) + COALESCE(stt, 0) +
                            COALESCE(exchange_charges, 0) + COALESCE(gst, 0) +
                            COALESCE(sebi_charges, 0) + COALESCE(stamp_duty, 0)), 0) AS total_charges
        FROM paper_trades WHERE status != 'OPEN'
    """))
    tot = total_pnl_result.mappings().first()
    total_pnl = float(tot["total_gross"] or 0) if tot else 0
    total_net_pnl = float(tot["total_net"] or 0) if tot else 0
    total_brokerage = float(tot["total_charges"] or 0) if tot else 0

    # Today's realized P&L (gross + net + today's charges)
    today_pnl_result = await db.execute(text("""
        SELECT COALESCE(SUM(pnl_amount), 0)                    AS today_gross,
               COALESCE(SUM(COALESCE(net_pnl, pnl_amount)), 0) AS today_net,
               COALESCE(SUM(COALESCE(brokerage, 0) + COALESCE(stt, 0) +
                            COALESCE(exchange_charges, 0) + COALESCE(gst, 0) +
                            COALESCE(sebi_charges, 0) + COALESCE(stamp_duty, 0)), 0) AS today_charges
        FROM paper_trades
        WHERE status != 'OPEN' AND DATE(exit_time) = :today
    """), {"today": today})
    td = today_pnl_result.mappings().first()
    today_pnl = float(td["today_gross"] or 0) if td else 0
    today_net_pnl = float(td["today_net"] or 0) if td else 0
    today_brokerage = float(td["today_charges"] or 0) if td else 0

    # Day limits
    day_status = await day_limits_engine.get_day_status(db, "PAPER")

    return {
        "success": True,
        "mode": "PAPER",
        "simulated_capital": capital,
        "available_margin": round(capital + total_net_pnl - open_exposure, 2),
        "open_exposure": round(open_exposure, 2),
        "open_trade_count": open_count,
        "total_realized_pnl": round(total_pnl, 2),
        "total_net_pnl": round(total_net_pnl, 2),
        "total_brokerage_paid": round(total_brokerage, 2),
        "net_pnl_today": round(today_pnl, 2),
        "today_gross_pnl": round(today_pnl, 2),
        "today_net_pnl_after_charges": round(today_net_pnl, 2),
        "today_brokerage": round(today_brokerage, 2),
        "loss_consumed_pct": day_status["loss_consumed_pct"],
        "profit_achieved_pct": day_status["profit_achieved_pct"],
        "day_loss_limit": day_status["day_loss_limit"],
        "day_profit_target": day_status["day_profit_target"],
        "trading_allowed": day_status["trading_allowed"],
        "stop_reason": day_status["stop_reason"],
    }


@router.get("/live")
async def get_live_funds(db: AsyncSession = Depends(get_db)):
    """Get live Fyers fund summary."""
    is_connected = fyers_client.is_authenticated()
    today = datetime.now(IST).date()

    # Today's live P&L (gross + net + charges)
    today_pnl_result = await db.execute(text("""
        SELECT COALESCE(SUM(gross_pnl), 0)                     AS today_gross,
               COALESCE(SUM(net_pnl), 0)                       AS today_net,
               COALESCE(SUM(COALESCE(brokerage, 0) + COALESCE(stt, 0) +
                            COALESCE(exchange_charges, 0) + COALESCE(gst, 0) +
                            COALESCE(sebi_charges, 0) + COALESCE(stamp_duty, 0)), 0) AS today_charges
        FROM live_trades
        WHERE status != 'OPEN' AND DATE(exit_time) = :today
    """), {"today": today})
    td = today_pnl_result.mappings().first()
    today_pnl = float(td["today_net"] or 0) if td else 0
    today_gross_pnl = float(td["today_gross"] or 0) if td else 0
    today_brokerage = float(td["today_charges"] or 0) if td else 0

    # Day limits
    day_status = await day_limits_engine.get_day_status(db, "LIVE")

    fund_data = {
        "success": True,
        "mode": "LIVE",
        "fyers_connected": is_connected,
        "total_balance": 0,
        "available_margin": 0,
        "utilized_margin": 0,
        "net_pnl_today": round(today_pnl, 2),
        "today_gross_pnl": round(today_gross_pnl, 2),
        "today_net_pnl_after_charges": round(today_pnl, 2),
        "today_brokerage": round(today_brokerage, 2),
        "loss_consumed_pct": day_status["loss_consumed_pct"],
        "profit_achieved_pct": day_status["profit_achieved_pct"],
        "day_loss_limit": day_status["day_loss_limit"],
        "day_profit_target": day_status["day_profit_target"],
        "trading_allowed": day_status["trading_allowed"],
        "stop_reason": day_status["stop_reason"],
    }

    if is_connected:
        try:
            funds_resp = await fyers_client.get_funds_async()
            if funds_resp and funds_resp.get("s") == "ok":
                fund_list = funds_resp.get("fund_limit", [])
                # Fyers v3 exposes balances as ``equityAmount`` /
                # ``commodityAmount`` per row. ``limitAmount`` was a v2
                # shape that no longer comes back — using it would zero
                # out the whole live funds panel.
                for f in fund_list:
                    fid = f.get("id")
                    amt = f.get("equityAmount")
                    if amt is None:
                        amt = f.get("limitAmount", 0)  # v2 fallback
                    if fid == 1:
                        fund_data["total_balance"] = amt or 0
                    elif fid == 2:
                        fund_data["utilized_margin"] = amt or 0
                    elif fid == 10:
                        fund_data["available_margin"] = amt or 0
                        if not fund_data.get("total_balance"):
                            fund_data["total_balance"] = amt or 0
        except Exception as e:
            logger.warning(f"Failed to fetch Fyers funds: {e}")

    return fund_data


@router.get("/combined")
async def get_combined_funds(db: AsyncSession = Depends(get_db)):
    """Get both paper and live funds in one response."""
    now = time.time()
    cached = _combined_funds_cache["data"]
    if cached is not None and now - _combined_funds_cache["timestamp"] < COMBINED_FUNDS_CACHE_TTL:
        return cached

    paper = await get_paper_funds(db)
    live = await get_live_funds(db)
    result = {
        "success": True,
        "paper": paper,
        "live": live,
    }
    _combined_funds_cache["data"] = result
    _combined_funds_cache["timestamp"] = now
    return result


@router.put("/paper/simulate")
async def simulate_paper_capital(
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """Update the paper-trading simulated capital (F4).

    Body: ``{"new_capital": 500000}``. Persists to
    ``trading_settings.simulated_capital`` and returns the fresh paper fund
    summary so the dashboard can update in one round-trip.
    """
    new_capital = body.get("new_capital")
    if new_capital is None:
        raise HTTPException(status_code=400, detail="new_capital is required")
    try:
        new_capital = float(new_capital)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="new_capital must be a number")
    if new_capital < 0:
        raise HTTPException(status_code=400, detail="new_capital cannot be negative")

    await db.execute(
        text(
            "UPDATE trading_settings SET simulated_capital = :cap, "
            "       updated_at = :ts WHERE id = 1"
        ),
        {"cap": new_capital, "ts": datetime.utcnow()},
    )
    await db.commit()

    # Invalidate the auto-trade engine's settings cache so the new capital
    # takes effect on the next scan tick without waiting for its TTL.
    try:
        from app import auto_trade_engine
        auto_trade_engine._cached_settings = None
    except Exception:
        pass

    # Bust the /combined cache so the dashboard doesn't show stale numbers.
    _combined_funds_cache["data"] = None
    _combined_funds_cache["timestamp"] = 0.0

    summary = await get_paper_funds(db)
    return {"success": True, "updated_capital": new_capital, "funds": summary}


@router.get("/paper/open-trades-charges")
async def paper_open_trades_charges(db: AsyncSession = Depends(get_db)):
    """Per-open-trade estimated round-trip charges (F3).

    Uses entry_price as both legs (no LTP available here) — the frontend
    multiplies by the current live-mark delta when it has an LTP. Keeps the
    dashboard's "Est. Charges" column populated even without a broker quote.
    """
    rows = (await db.execute(text("""
        SELECT id, symbol, entry_price, quantity,
               COALESCE(product_type, 'INTRADAY') AS product_type
          FROM paper_trades WHERE status = 'OPEN'
    """))).mappings().all()
    out = []
    for r in rows:
        entry = float(r["entry_price"] or 0)
        qty = int(r["quantity"] or 0)
        est = estimate_round_trip_cost(entry, qty, entry, r["product_type"])
        out.append({
            "trade_id": r["id"],
            "symbol": r["symbol"],
            "product_type": r["product_type"],
            "estimated_charges": round(est, 2),
        })
    return out
