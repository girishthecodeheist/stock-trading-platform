"""Day P&L Limits API endpoints."""

import logging
from datetime import datetime
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.day_limits_engine import day_limits_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/limits", tags=["Day Limits"])


class LimitsUpdate(BaseModel):
    day_max_loss: Optional[float] = None
    day_profit_target: Optional[float] = None


@router.get("/status")
async def get_limits_status(db: AsyncSession = Depends(get_db)):
    """Get day P&L status for both modes."""
    paper = await day_limits_engine.get_day_status(db, "PAPER")
    live = await day_limits_engine.get_day_status(db, "LIVE")
    return {"success": True, "paper": paper, "live": live}


@router.get("/status/paper")
async def get_paper_limits(db: AsyncSession = Depends(get_db)):
    """Get paper day limit status."""
    status = await day_limits_engine.get_day_status(db, "PAPER")
    return {"success": True, **status}


@router.get("/status/live")
async def get_live_limits(db: AsyncSession = Depends(get_db)):
    """Get live day limit status."""
    status = await day_limits_engine.get_day_status(db, "LIVE")
    return {"success": True, **status}


@router.put("/paper")
async def update_paper_limits(body: LimitsUpdate, db: AsyncSession = Depends(get_db)):
    """Update paper day limits."""
    updates = {}
    if body.day_max_loss is not None:
        updates["day_max_loss_paper"] = body.day_max_loss
    if body.day_profit_target is not None:
        updates["day_profit_target_paper"] = body.day_profit_target
    if not updates:
        return {"success": False, "error": "No fields to update"}

    set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
    await db.execute(text(f"UPDATE trading_settings SET {set_clauses} WHERE id=1"), updates)
    await db.commit()
    return {"success": True, "updated": updates}


@router.put("/live")
async def update_live_limits(body: LimitsUpdate, db: AsyncSession = Depends(get_db)):
    """Update live day limits."""
    updates = {}
    if body.day_max_loss is not None:
        updates["day_max_loss_live"] = body.day_max_loss
    if body.day_profit_target is not None:
        updates["day_profit_target_live"] = body.day_profit_target
    if not updates:
        return {"success": False, "error": "No fields to update"}

    set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
    await db.execute(text(f"UPDATE trading_settings SET {set_clauses} WHERE id=1"), updates)
    await db.commit()
    return {"success": True, "updated": updates}


@router.post("/reset")
async def reset_daily_limits(db: AsyncSession = Depends(get_db)):
    """Manual reset of day limits."""
    await db.execute(text("""
        UPDATE trading_settings SET
            trading_halted_paper = false,
            trading_halted_live = false,
            halt_reason = NULL,
            day_limits_reset_at = :now
        WHERE id=1
    """), {"now": datetime.utcnow()})
    await db.commit()
    return {"success": True, "message": "Day limits reset"}
