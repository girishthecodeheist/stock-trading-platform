"""Settings API endpoints."""

import math
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/settings", tags=["Settings"])


class SettingsUpdate(BaseModel):
    simulated_capital: Optional[float] = None
    default_sl_percent: Optional[float] = None
    default_target_percent: Optional[float] = None
    default_quantity: Optional[int] = None
    max_open_trades: Optional[int] = None
    allow_duplicate_symbol: Optional[bool] = None
    day_max_loss_paper: Optional[float] = None
    day_profit_target_paper: Optional[float] = None
    day_max_loss_live: Optional[float] = None
    day_profit_target_live: Optional[float] = None
    scan_frequency_minutes: Optional[int] = None
    top_movers_count: Optional[int] = None
    min_change_pct: Optional[float] = None
    min_volume: Optional[int] = None
    auto_trade_enabled: Optional[bool] = None
    auto_quantity_enabled: Optional[bool] = None


@router.get("")
async def get_settings(db: AsyncSession = Depends(get_db)):
    """Get all trading settings."""
    result = await db.execute(text("SELECT * FROM trading_settings WHERE id=1"))
    row = result.mappings().first()
    if not row:
        return {"success": False, "error": "Settings not initialized"}
    return {"success": True, "settings": dict(row)}


@router.put("")
async def update_settings(body: SettingsUpdate, db: AsyncSession = Depends(get_db)):
    """Update trading settings."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        return {"success": False, "error": "No fields to update"}

    set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
    updates["now"] = datetime.utcnow()
    await db.execute(text(
        f"UPDATE trading_settings SET {set_clauses}, updated_at = :now WHERE id=1"
    ), updates)
    await db.commit()
    logger.info(f"Settings updated: {list(updates.keys())}")
    return {"success": True, "updated": list(updates.keys())}


@router.get("/calculate-quantity")
async def calculate_quantity(
    entry_price: float = Query(..., description="Entry price of the stock"),
    sl_percent: Optional[float] = Query(None, description="Stop loss % (overrides settings default)"),
    target_percent: Optional[float] = Query(None, description="Target % (overrides settings default)"),
    mode: str = Query("PAPER", description="PAPER or LIVE"),
    db: AsyncSession = Depends(get_db),
):
    """Auto-calculate optimal trade quantity based on P&L limits and share price.

    Logic:
    - qty_from_loss = abs(day_max_loss) / (entry_price * sl_percent / 100)
    - qty_from_profit = day_profit_target / (entry_price * target_percent / 100)
    - quantity = min(qty_from_loss, qty_from_profit)
    - Also ensure: quantity * entry_price <= available_margin (simulated_capital)
    """
    result = await db.execute(text("SELECT * FROM trading_settings WHERE id=1"))
    row = result.mappings().first()
    if not row:
        return {"success": False, "error": "Settings not initialized"}

    settings = dict(row)
    sl_pct = sl_percent if sl_percent is not None else settings["default_sl_percent"]
    tgt_pct = target_percent if target_percent is not None else settings["default_target_percent"]
    capital = settings["simulated_capital"]

    if mode.upper() == "LIVE":
        max_loss = abs(settings["day_max_loss_live"])
        profit_target = settings["day_profit_target_live"]
    else:
        max_loss = abs(settings["day_max_loss_paper"])
        profit_target = settings["day_profit_target_paper"]

    # Calculate per-share risk and reward
    sl_per_share = entry_price * sl_pct / 100.0
    target_per_share = entry_price * tgt_pct / 100.0

    if sl_per_share <= 0 or target_per_share <= 0:
        return {"success": False, "error": "SL and Target must be positive"}

    # Quantity limited by max loss
    qty_from_loss = max_loss / sl_per_share
    # Quantity to achieve profit target
    qty_from_profit = profit_target / target_per_share
    # Quantity limited by available capital
    qty_from_capital = capital / entry_price if entry_price > 0 else 0

    # Take the minimum to satisfy all constraints
    optimal_qty = int(math.floor(min(qty_from_loss, qty_from_profit, qty_from_capital)))
    optimal_qty = max(optimal_qty, 1)  # At least 1

    return {
        "success": True,
        "quantity": optimal_qty,
        "entry_price": entry_price,
        "sl_percent": sl_pct,
        "target_percent": tgt_pct,
        "sl_per_share": round(sl_per_share, 2),
        "target_per_share": round(target_per_share, 2),
        "potential_loss": round(optimal_qty * sl_per_share, 2),
        "potential_profit": round(optimal_qty * target_per_share, 2),
        "total_investment": round(optimal_qty * entry_price, 2),
        "max_loss_limit": max_loss,
        "profit_target": profit_target,
        "capital": capital,
        "breakdown": {
            "qty_from_loss_limit": int(math.floor(qty_from_loss)),
            "qty_from_profit_target": int(math.floor(qty_from_profit)),
            "qty_from_capital": int(math.floor(qty_from_capital)),
        }
    }
