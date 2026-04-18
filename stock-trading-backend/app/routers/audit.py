"""Audit-trail API router (F5).

Exposes read-only endpoints over ``trade_audit_log`` so the Trade Journal
UI, daily reports, and ad-hoc investigations can reconstruct the full
lifecycle of any trade.
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter(prefix="/api/audit", tags=["audit"])


def _row_to_event(r) -> dict:
    return {
        "id": r[0],
        "trade_id": r[1],
        "trade_type": r[2],
        "event_type": r[3],
        "symbol": r[4],
        "timestamp": str(r[5]) if r[5] else None,
        "old_value": r[6],
        "new_value": r[7],
        "reason": r[8],
        "trigger_data": r[9],
        "metadata": r[10],
    }


_BASE_SELECT = (
    "SELECT id, trade_id, trade_type, event_type, symbol, timestamp, "
    "       old_value, new_value, reason, trigger_data, extra_metadata "
    "  FROM trade_audit_log"
)


@router.get("/trade/{trade_id}")
async def trade_audit(
    trade_id: int,
    trade_type: str = Query("PAPER", pattern="^(PAPER|LIVE)$"),
    db: AsyncSession = Depends(get_db),
):
    """Full audit trail for a single trade, oldest first."""
    q = text(
        _BASE_SELECT
        + " WHERE trade_id = :id AND trade_type = :tt "
        + " ORDER BY timestamp ASC, id ASC"
    )
    rows = (await db.execute(q, {"id": trade_id, "tt": trade_type.upper()})).fetchall()
    return [_row_to_event(r) for r in rows]


@router.get("/daily")
async def daily_audit(
    date: Optional[str] = Query(None, description="YYYY-MM-DD (default: today UTC)"),
    event_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """All audit events for a given calendar day."""
    try:
        day = datetime.strptime(date, "%Y-%m-%d").date() if date else datetime.utcnow().date()
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)

    clauses = ["timestamp >= :start", "timestamp < :end"]
    params = {"start": start, "end": end}
    if event_type:
        clauses.append("event_type = :event_type")
        params["event_type"] = event_type.upper()

    q = text(
        _BASE_SELECT
        + " WHERE " + " AND ".join(clauses)
        + " ORDER BY timestamp ASC, id ASC"
    )
    rows = (await db.execute(q, params)).fetchall()
    return [_row_to_event(r) for r in rows]


@router.get("/sl-changes")
async def sl_change_history(
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD inclusive"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD inclusive"),
    symbol: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """All SL / target / trailing-profit changes in a window.

    Matches on the canonical event_type set used by the engine:
    ``SL_CHANGED``, ``TARGET_CHANGED``, ``TRAILING_PROFIT``,
    ``TREND_REVERSAL``, ``REANALYSIS``.
    """
    events = [
        "SL_CHANGED", "TARGET_CHANGED", "TRAILING_PROFIT",
        "TREND_REVERSAL", "REANALYSIS",
    ]
    clauses = ["event_type = ANY(:events)"]
    params: dict = {"events": events}

    if date_from:
        try:
            clauses.append("timestamp >= :start")
            params["start"] = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="date_from must be YYYY-MM-DD")
    if date_to:
        try:
            end = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            clauses.append("timestamp < :end")
            params["end"] = end
        except ValueError:
            raise HTTPException(status_code=400, detail="date_to must be YYYY-MM-DD")
    if symbol:
        clauses.append("symbol = :symbol")
        params["symbol"] = symbol

    q = text(
        _BASE_SELECT
        + " WHERE " + " AND ".join(clauses)
        + " ORDER BY timestamp DESC, id DESC"
        + " LIMIT 500"
    )
    rows = (await db.execute(q, params)).fetchall()
    return [_row_to_event(r) for r in rows]
