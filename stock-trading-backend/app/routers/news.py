"""News API endpoints — aggregated headlines + sentiment.

Thin HTTP layer over :mod:`app.news_engine`. Each endpoint returns the
same per-symbol payload shape that :func:`news_engine.get_news_sentiment`
produces so the frontend can share a single rendering pipeline for market
news, watchlist news, and single-symbol lookups.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from sqlalchemy import text

from app.database import async_session_factory
from app.news_engine import get_news_sentiment


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/news", tags=["News"])


# Broad-market indices and bellwether symbols we pull when no specific
# symbol is requested. Keeps the "market" tab useful even when the
# heatmap poller hasn't populated top movers yet.
_MARKET_INDICES: List[str] = [
    "NSE:NIFTY50-INDEX",
    "NSE:NIFTYBANK-INDEX",
    "NSE:RELIANCE-EQ",
    "NSE:HDFCBANK-EQ",
    "NSE:INFY-EQ",
]


async def _gather_for_symbols(symbols: List[str]) -> List[Dict[str, Any]]:
    """Fetch sentiment for each symbol in parallel; skip failing rows."""
    if not symbols:
        return []

    async def _one(sym: str) -> Optional[Dict[str, Any]]:
        try:
            data = await get_news_sentiment(sym)
        except Exception as e:
            logger.debug(f"News fetch failed for {sym}: {e}")
            return None
        data = dict(data or {})
        data["symbol"] = sym
        return data

    results = await asyncio.gather(*[_one(s) for s in symbols], return_exceptions=False)
    return [r for r in results if r is not None]


async def _top_mover_symbols(limit: int = 8) -> List[str]:
    """Return symbols from the latest heatmap snapshot, top movers first.

    Falls back to an empty list if the heatmap table is empty / missing.
    """
    try:
        async with async_session_factory() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT symbol FROM nse_heatmap_snapshots "
                        "WHERE fetched_at = (SELECT MAX(fetched_at) FROM nse_heatmap_snapshots) "
                        "ORDER BY ABS(change_pct) DESC LIMIT :limit"
                    ),
                    {"limit": limit},
                )
            ).fetchall()
            return [r[0] for r in rows]
    except Exception as e:
        logger.debug(f"Top mover lookup failed: {e}")
        return []


@router.get("/feed")
async def news_feed(
    limit: int = Query(8, ge=1, le=20, description="Number of symbols to aggregate"),
) -> Dict[str, Any]:
    """Aggregated headlines for the current top movers.

    Uses the most recent heatmap snapshot to pick ``limit`` symbols with
    the biggest absolute ``change_pct``. If the heatmap is empty we fall
    back to the broad-market bellwethers so the feed is never blank.
    """
    symbols = await _top_mover_symbols(limit=limit)
    if not symbols:
        symbols = _MARKET_INDICES[:limit]
    items = await _gather_for_symbols(symbols)
    return {
        "symbols": symbols,
        "count": len(items),
        "items": items,
    }


@router.get("/symbol/{symbol:path}")
async def news_for_symbol(symbol: str) -> Dict[str, Any]:
    """News + sentiment for a single symbol.

    ``symbol`` is accepted verbatim (e.g. ``RELIANCE``, ``NSE:RELIANCE-EQ``)
    — :func:`news_engine.get_news_sentiment` handles the ``NSE:`` / ``-EQ``
    normalisation internally.
    """
    try:
        data = await get_news_sentiment(symbol)
    except Exception as e:
        logger.warning(f"News lookup failed for {symbol}: {e}")
        data = {
            "headlines": [],
            "headline_count": 0,
            "avg_sentiment": 0.0,
            "sentiment_classification": "NEUTRAL",
            "sentiment_score": 0.0,
        }
    payload = dict(data or {})
    payload["symbol"] = symbol
    return payload


@router.get("/market")
async def market_news() -> Dict[str, Any]:
    """Broad-market news: indices + the largest index constituents.

    Bypasses the heatmap so the Market tab has stable content even on a
    fresh install where the poller hasn't run yet.
    """
    items = await _gather_for_symbols(_MARKET_INDICES)
    return {
        "symbols": _MARKET_INDICES,
        "count": len(items),
        "items": items,
    }
