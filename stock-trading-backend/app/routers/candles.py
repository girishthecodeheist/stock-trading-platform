"""Candles API router - serves OHLCV data with timeframe support.
Uses Fyers live data when authenticated, falls back to database."""

import logging
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.database import get_db
from app import fyers_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/candles", tags=["candles"])


@router.get("")
async def get_candles(
    symbol: str = Query(..., description="Symbol e.g. NSE:RELIANCE-EQ"),
    timeframe: str = Query("1D", description="Timeframe: 1m, 5m, 15m, 1D, 1W, 1Y"),
    limit: int = Query(300, description="Max candles to return"),
    live: bool = Query(True, description="Try Fyers live data first"),
    db: AsyncSession = Depends(get_db),
):
    """Get OHLCV candles for a symbol and timeframe.
    When Fyers is authenticated, fetches fresh data from Fyers API.
    Falls back to database if Fyers is unavailable."""
    source = "database"

    # Try Fyers live data first
    if live and fyers_client.is_authenticated():
        days_map = {"1m": 7, "5m": 30, "15m": 60, "1D": 365, "1W": 730, "1Y": 365}
        days = days_map.get(timeframe, 365)
        fyers_candles = await fyers_client.get_historical_data_async(symbol, timeframe, days)
        
        if fyers_candles and len(fyers_candles) > 0:
            source = "fyers_live"
            # Also store in DB for caching
            for c in fyers_candles[-50:]:  # Store last 50 for cache
                try:
                    await db.execute(text("""
                        INSERT INTO ohlcv_candles (symbol, timestamp, timeframe, open, high, low, close, volume)
                        VALUES (:symbol, :timestamp, :timeframe, :open, :high, :low, :close, :volume)
                        ON CONFLICT (symbol, timestamp, timeframe) DO UPDATE SET
                            open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                            close = EXCLUDED.close, volume = EXCLUDED.volume
                    """), {
                        "symbol": symbol, "timestamp": c["timestamp"], "timeframe": timeframe,
                        "open": c["open"], "high": c["high"], "low": c["low"],
                        "close": c["close"], "volume": c["volume"],
                    })
                except Exception:
                    pass
            try:
                await db.commit()
            except Exception:
                await db.rollback()

            candles = [
                {
                    "time": c["timestamp"],
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                    "volume": c["volume"],
                }
                for c in fyers_candles[-limit:]
            ]
            return {"candles": candles, "symbol": symbol, "timeframe": timeframe, "count": len(candles), "source": source}

    # Fallback to database
    query = text("""
        SELECT timestamp, open, high, low, close, volume
        FROM ohlcv_candles
        WHERE symbol = :symbol AND timeframe = :timeframe
        ORDER BY timestamp DESC
        LIMIT :limit
    """)

    result = await db.execute(query, {
        "symbol": symbol,
        "timeframe": timeframe,
        "limit": limit,
    })
    rows = result.fetchall()

    candles = [
        {
            "time": str(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": int(r[5]),
        }
        for r in reversed(rows)  # Reverse to chronological order
    ]

    return {"candles": candles, "symbol": symbol, "timeframe": timeframe, "count": len(candles), "source": source}
