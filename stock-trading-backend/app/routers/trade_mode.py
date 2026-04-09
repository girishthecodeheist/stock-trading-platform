"""Trade Mode API endpoints - Paper vs Live switching."""

import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.day_limits_engine import day_limits_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/trade-mode", tags=["Trade Mode"])


class TradeModeUpdate(BaseModel):
    mode: str  # "PAPER" or "LIVE"


@router.get("")
async def get_trade_mode(db: AsyncSession = Depends(get_db)):
    """Get current trade mode."""
    result = await db.execute(text("SELECT trade_mode FROM trading_settings WHERE id=1"))
    row = result.scalar()
    return {"mode": row or "PAPER"}


@router.put("")
async def set_trade_mode(body: TradeModeUpdate, db: AsyncSession = Depends(get_db)):
    """Set trade mode (PAPER or LIVE)."""
    mode = body.mode.upper()
    if mode not in ("PAPER", "LIVE"):
        return {"success": False, "error": "Mode must be PAPER or LIVE"}

    await db.execute(text(
        "UPDATE trading_settings SET trade_mode = :mode WHERE id=1"
    ), {"mode": mode})
    await db.commit()
    logger.info(f"Trade mode switched to {mode}")
    return {"success": True, "mode": mode}


@router.get("/status")
async def get_trade_mode_status(db: AsyncSession = Depends(get_db)):
    """Get engine status, constraints, day limits for both modes."""
    result = await db.execute(text("SELECT * FROM trading_settings WHERE id=1"))
    settings = result.mappings().first()
    if not settings:
        return {"success": False, "error": "Settings not initialized"}

    paper_status = await day_limits_engine.get_day_status(db, "PAPER")
    live_status = await day_limits_engine.get_day_status(db, "LIVE")

    return {
        "success": True,
        "current_mode": settings["trade_mode"],
        "paper_limits": paper_status,
        "live_limits": live_status,
        "settings": {
            "simulated_capital": settings["simulated_capital"],
            "max_open_trades": settings["max_open_trades"],
            "auto_trade_enabled": settings["auto_trade_enabled"],
            "scanner_running": settings["scanner_running"],
        },
    }
