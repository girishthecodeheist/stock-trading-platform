"""Funds API endpoints - Paper and Live fund management."""

import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app import fyers_client
from app.day_limits_engine import day_limits_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/funds", tags=["Funds"])

IST = timezone(timedelta(hours=5, minutes=30))


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

    # Total realized P&L
    total_pnl_result = await db.execute(text("""
        SELECT COALESCE(SUM(pnl_amount), 0) as total_pnl
        FROM paper_trades WHERE status != 'OPEN'
    """))
    total_pnl = float(total_pnl_result.scalar() or 0)

    # Today's realized P&L
    today_pnl_result = await db.execute(text("""
        SELECT COALESCE(SUM(pnl_amount), 0) as today_pnl
        FROM paper_trades
        WHERE status != 'OPEN' AND DATE(exit_time) = :today
    """), {"today": today})
    today_pnl = float(today_pnl_result.scalar() or 0)

    # Day limits
    day_status = await day_limits_engine.get_day_status(db, "PAPER")

    return {
        "success": True,
        "mode": "PAPER",
        "simulated_capital": capital,
        "available_margin": round(capital + total_pnl - open_exposure, 2),
        "open_exposure": round(open_exposure, 2),
        "open_trade_count": open_count,
        "total_realized_pnl": round(total_pnl, 2),
        "net_pnl_today": round(today_pnl, 2),
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

    # Today's live P&L
    today_pnl_result = await db.execute(text("""
        SELECT COALESCE(SUM(net_pnl), 0) as today_pnl
        FROM live_trades
        WHERE status != 'OPEN' AND DATE(exit_time) = :today
    """), {"today": today})
    today_pnl = float(today_pnl_result.scalar() or 0)

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
        "loss_consumed_pct": day_status["loss_consumed_pct"],
        "profit_achieved_pct": day_status["profit_achieved_pct"],
        "day_loss_limit": day_status["day_loss_limit"],
        "day_profit_target": day_status["day_profit_target"],
        "trading_allowed": day_status["trading_allowed"],
        "stop_reason": day_status["stop_reason"],
    }

    if is_connected:
        try:
            funds_resp = fyers_client.get_funds()
            if funds_resp and funds_resp.get("s") == "ok":
                fund_list = funds_resp.get("fund_limit", [])
                for f in fund_list:
                    fid = f.get("id")
                    if fid == 10:
                        fund_data["total_balance"] = f.get("equityAmount", 0)
                        fund_data["available_margin"] = f.get("limitAmount", 0)
                    elif fid == 11:
                        fund_data["utilized_margin"] = f.get("limitAmount", 0)
        except Exception as e:
            logger.warning(f"Failed to fetch Fyers funds: {e}")

    return fund_data


@router.get("/combined")
async def get_combined_funds(db: AsyncSession = Depends(get_db)):
    """Get both paper and live funds in one response."""
    paper = await get_paper_funds(db)
    live = await get_live_funds(db)
    return {
        "success": True,
        "paper": paper,
        "live": live,
    }
