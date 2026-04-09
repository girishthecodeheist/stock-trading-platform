"""Instruments API router - search and list instruments across all asset types."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.database import get_db

router = APIRouter(prefix="/api/instruments", tags=["instruments"])


@router.get("")
async def list_instruments(
    segment: Optional[str] = Query(None, description="Filter by segment: EQUITY, FUTURE, OPTION, INDEX"),
    exchange: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Search by symbol or name"),
    limit: int = Query(100),
    db: AsyncSession = Depends(get_db),
):
    """List all instruments with optional filters."""
    clauses = ["1=1"]
    params = {}

    if segment:
        clauses.append("segment = :segment")
        params["segment"] = segment.upper()
    if exchange:
        clauses.append("exchange = :exchange")
        params["exchange"] = exchange.upper()
    if search:
        clauses.append("(LOWER(symbol) LIKE :search OR LOWER(name) LIKE :search)")
        params["search"] = f"%{search.lower()}%"

    where = " AND ".join(clauses)
    query = text(f"""
        SELECT id, symbol, exchange, segment, name, lot_size, tick_size, 
               expiry, strike, option_type, is_active, approx_price
        FROM instruments
        WHERE {where}
        ORDER BY name
        LIMIT :limit
    """)
    params["limit"] = limit

    result = await db.execute(query, params)
    rows = result.fetchall()

    return [
        {
            "id": r[0], "symbol": r[1], "exchange": r[2], "segment": r[3],
            "name": r[4], "lot_size": r[5], "tick_size": r[6],
            "expiry": r[7], "strike": r[8], "option_type": r[9],
            "is_active": r[10], "approx_price": r[11],
        }
        for r in rows
    ]


@router.get("/segments")
async def get_segments():
    """Get available instrument segments."""
    return {
        "segments": [
            {"id": "EQUITY", "name": "Stocks (Equity)", "description": "NSE/BSE listed stocks"},
            {"id": "INDEX", "name": "Indices", "description": "NIFTY 50, Bank NIFTY, etc."},
            {"id": "FUTURE", "name": "Futures (F&O)", "description": "Stock and index futures"},
            {"id": "OPTION", "name": "Options", "description": "Call and put options"},
        ]
    }
