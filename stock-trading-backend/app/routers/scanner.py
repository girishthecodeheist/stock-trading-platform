"""Scanner API endpoints - signal generation from heatmap + auto-trade engine control."""

import json
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session_factory
from app.heatmap_poller import heatmap_poller
from app import auto_trade_engine
from app import fyers_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/scanner", tags=["Scanner"])

IST = timezone(timedelta(hours=5, minutes=30))


@router.get("/status")
async def get_scanner_status(db: AsyncSession = Depends(get_db)):
    """Get scanner running/paused status + auto-trade engine status."""
    result = await db.execute(text(
        "SELECT scanner_running, auto_trade_enabled, scan_frequency_minutes FROM trading_settings WHERE id=1"
    ))
    row = result.mappings().first()
    engine_status = auto_trade_engine.get_engine_status()
    return {
        "success": True,
        "scanner_running": row["scanner_running"] if row else False,
        "auto_trade_enabled": row["auto_trade_enabled"] if row else False,
        "scan_frequency": row["scan_frequency_minutes"] if row else 5,
        "last_scan": heatmap_poller.last_poll_time.isoformat() if heatmap_poller.last_poll_time else None,
        "universe_size": len(heatmap_poller.active_movers),
        "engine": engine_status,
    }


@router.put("/toggle")
async def toggle_scanner(db: AsyncSession = Depends(get_db)):
    """Start/pause scanner."""
    result = await db.execute(text("SELECT scanner_running FROM trading_settings WHERE id=1"))
    current = result.scalar()
    new_val = not current if current is not None else True
    await db.execute(text(
        "UPDATE trading_settings SET scanner_running = :val WHERE id=1"
    ), {"val": new_val})
    await db.commit()
    return {"success": True, "scanner_running": new_val}


@router.put("/auto-trade/toggle")
async def toggle_auto_trade(db: AsyncSession = Depends(get_db)):
    """Enable/disable auto-trade engine."""
    result = await db.execute(text("SELECT auto_trade_enabled FROM trading_settings WHERE id=1"))
    current = result.scalar()
    new_val = not current if current is not None else True
    await db.execute(text(
        "UPDATE trading_settings SET auto_trade_enabled = :val WHERE id=1"
    ), {"val": new_val})
    await db.commit()

    if new_val:
        auto_trade_engine.start_engine()
    else:
        auto_trade_engine.stop_engine()

    return {"success": True, "auto_trade_enabled": new_val}


@router.get("/auto-trade/status")
async def get_auto_trade_status():
    """Get auto-trade engine status and recent log."""
    return auto_trade_engine.get_engine_status()


@router.get("/auto-trade/log")
async def get_auto_trade_log():
    """Get auto-trade engine recent activity log."""
    status = auto_trade_engine.get_engine_status()
    return {"log": status.get("recent_log", []), "engine_running": status["engine_running"], "daily_target_met": status.get("daily_target_met", False)}


@router.get("/universe")
async def get_scan_universe():
    """Get current scan universe (symbols from heatmap)."""
    stocks = heatmap_poller.get_all_stocks()
    return {
        "success": True,
        "count": len(stocks),
        "symbols": [s["symbol"] for s in stocks],
        "stocks": stocks[:100],
    }


@router.get("/auto-trade/events")
async def stream_auto_trade_events():
    """SSE stream of auto-trade events (TRADE_PLACED, TRADE_CLOSED, SIGNALS_UPDATED, DAILY_TARGET_MET)."""
    async def event_generator():
        q = auto_trade_engine.subscribe_sse()
        try:
            while True:
                event = await q.get()
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:
            auto_trade_engine.unsubscribe_sse(q)
        except Exception as e:
            logger.error(f"Auto-trade SSE error: {e}")
            auto_trade_engine.unsubscribe_sse(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/auto-trade/signals")
async def get_auto_trade_signals():
    """Get the last computed signals from the auto-trade engine (top 20 stocks)."""
    signals = auto_trade_engine.get_last_signals()
    return {"count": len(signals), "signals": signals}


@router.get("/open-trades/stream")
async def stream_open_trades_prices(
    interval: int = Query(2, description="Update interval in seconds (min 2)"),
):
    """SSE stream of live prices for all symbols with open trades."""
    if not fyers_client.is_authenticated():
        return {"status": "error", "message": "Fyers not authenticated"}

    interval = max(2, interval)

    async def event_generator():
        try:
            while True:
                async with async_session_factory() as db:
                    result = await db.execute(text("""
                        SELECT DISTINCT symbol FROM paper_trades WHERE status = 'OPEN'
                    """))
                    symbols = [r[0] for r in result.fetchall()]

                if not symbols:
                    yield f"data: {json.dumps({'prices': {}, 'open_count': 0, 'timestamp': datetime.now(IST).isoformat()})}\n\n"
                    await asyncio.sleep(interval)
                    continue

                all_prices = {}
                batch_size = 50
                for i in range(0, len(symbols), batch_size):
                    batch = symbols[i:i + batch_size]
                    try:
                        prices = fyers_client.get_live_prices_batch(batch)
                        all_prices.update(prices)
                    except Exception as e:
                        logger.error(f"SSE price fetch error: {e}")

                yield f"data: {json.dumps({'prices': all_prices, 'open_count': len(symbols), 'timestamp': datetime.now(IST).isoformat()})}\n\n"
                await asyncio.sleep(interval)

        except asyncio.CancelledError:
            logger.info("Open trades SSE stream cancelled")
        except Exception as e:
            logger.error(f"Open trades SSE error: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
