"""Fyers API router - OAuth flow, live quotes, historical data, SSE streaming."""

import json
import asyncio
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app import fyers_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fyers", tags=["fyers"])


@router.get("/status")
async def fyers_status():
    """Check if Fyers API is authenticated and ready."""
    authenticated = fyers_client.is_authenticated()
    result = {
        "authenticated": authenticated,
        "app_id": fyers_client.FYERS_APP_ID,
        "redirect_uri": fyers_client.FYERS_REDIRECT_URI,
    }
    if authenticated:
        profile = fyers_client.get_profile()
        if profile.get("s") == "ok":
            result["profile"] = profile.get("data", {})
    return result


@router.get("/auth-url")
async def get_auth_url():
    """Get the Fyers OAuth login URL. User should open this in browser to authenticate."""
    url = fyers_client.get_auth_url()
    return {"auth_url": url, "instructions": "Open this URL in your browser, login with your Fyers credentials, and you will be redirected back with the access token."}


@router.get("/callback")
async def fyers_callback(
    auth_code: Optional[str] = Query(None),
    code: Optional[str] = Query(None),
    s: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
):
    """OAuth callback - Fyers redirects here after user login."""
    # Fyers sends auth_code as either 'auth_code' or 'code' parameter
    token_code = auth_code or code
    
    if not token_code:
        return HTMLResponse(content="""
        <html>
        <head><title>Fyers Auth - Error</title>
        <style>body{font-family:sans-serif;background:#0a0e17;color:#fff;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
        .card{background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:40px;text-align:center;max-width:500px}
        h1{color:#ef4444} p{color:#94a3b8}</style>
        </head>
        <body><div class="card">
        <h1>Authentication Failed</h1>
        <p>No authorization code received from Fyers.</p>
        <p>Please try again from the trading platform.</p>
        </div></body></html>
        """, status_code=400)

    result = fyers_client.generate_access_token(token_code)
    
    if result.get("status") == "ok":
        return HTMLResponse(content="""
        <html>
        <head><title>Fyers Auth - Success</title>
        <style>body{font-family:sans-serif;background:#0a0e17;color:#fff;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
        .card{background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:40px;text-align:center;max-width:500px}
        h1{color:#22c55e} p{color:#94a3b8} .btn{display:inline-block;margin-top:20px;padding:12px 24px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none}</style>
        </head>
        <body><div class="card">
        <h1>Authentication Successful!</h1>
        <p>Your Fyers account is now connected. Live market data is ready.</p>
        <p>You can close this tab and return to the trading platform.</p>
        <a class="btn" href="http://localhost:4200">Back to TradeIQ</a>
        </div></body></html>
        """)
    else:
        return HTMLResponse(content=f"""
        <html>
        <head><title>Fyers Auth - Error</title>
        <style>body{{font-family:sans-serif;background:#0a0e17;color:#fff;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
        .card{{background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:40px;text-align:center;max-width:500px}}
        h1{{color:#ef4444}} p{{color:#94a3b8}} code{{background:rgba(255,255,255,0.1);padding:4px 8px;border-radius:4px;font-size:14px}}</style>
        </head>
        <body><div class="card">
        <h1>Authentication Failed</h1>
        <p>{result.get('message', 'Unknown error')}</p>
        <p>Please try again from the trading platform.</p>
        </div></body></html>
        """, status_code=400)


@router.get("/quotes")
async def get_quotes(
    symbols: str = Query(..., description="Comma-separated Fyers symbols e.g. NSE:RELIANCE-EQ,NSE:TCS-EQ"),
):
    """Get live quotes for given symbols."""
    if not fyers_client.is_authenticated():
        return {"status": "error", "message": "Fyers not authenticated. Call /api/fyers/auth-url first."}

    symbol_list = [s.strip() for s in symbols.split(",")]
    return fyers_client.get_quotes(symbol_list)


@router.get("/history")
async def get_fyers_history(
    symbol: str = Query(..., description="Fyers symbol e.g. NSE:RELIANCE-EQ"),
    timeframe: str = Query("1D", description="1m, 5m, 15m, 1D, 1W"),
    days: int = Query(365, description="Days of history to fetch"),
    db: AsyncSession = Depends(get_db),
):
    """Fetch historical data from Fyers and store in database."""
    if not fyers_client.is_authenticated():
        return {"status": "error", "message": "Fyers not authenticated"}

    candles = fyers_client.get_historical_data(symbol, timeframe, days)
    
    if not candles:
        return {"status": "error", "message": "No data returned from Fyers", "symbol": symbol}

    # Store in database (upsert)
    stored = 0
    for c in candles:
        try:
            await db.execute(text("""
                INSERT INTO ohlcv_candles (symbol, timestamp, timeframe, open, high, low, close, volume)
                VALUES (:symbol, :timestamp, :timeframe, :open, :high, :low, :close, :volume)
                ON CONFLICT (symbol, timestamp, timeframe) DO UPDATE SET
                    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                    close = EXCLUDED.close, volume = EXCLUDED.volume
            """), {
                "symbol": symbol,
                "timestamp": c["timestamp"],
                "timeframe": timeframe,
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c["volume"],
            })
            stored += 1
        except Exception as e:
            logger.error(f"Error storing candle: {e}")

    await db.commit()

    return {
        "status": "ok",
        "symbol": symbol,
        "timeframe": timeframe,
        "fetched": len(candles),
        "stored": stored,
    }


@router.get("/sync-all")
async def sync_all_instruments(
    timeframe: str = Query("1D"),
    days: int = Query(365),
    db: AsyncSession = Depends(get_db),
):
    """Sync historical data for all instruments from Fyers."""
    if not fyers_client.is_authenticated():
        return {"status": "error", "message": "Fyers not authenticated"}

    # Get all instrument symbols
    result = await db.execute(text("SELECT symbol FROM instruments WHERE is_active = true"))
    rows = result.fetchall()
    symbols = [r[0] for r in rows]

    results = []
    for symbol in symbols:
        candles = fyers_client.get_historical_data(symbol, timeframe, days)
        stored = 0
        for c in candles:
            try:
                await db.execute(text("""
                    INSERT INTO ohlcv_candles (symbol, timestamp, timeframe, open, high, low, close, volume)
                    VALUES (:symbol, :timestamp, :timeframe, :open, :high, :low, :close, :volume)
                    ON CONFLICT (symbol, timestamp, timeframe) DO UPDATE SET
                        open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                        close = EXCLUDED.close, volume = EXCLUDED.volume
                """), {
                    "symbol": symbol,
                    "timestamp": c["timestamp"],
                    "timeframe": timeframe,
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                    "volume": c["volume"],
                })
                stored += 1
            except Exception as e:
                pass
        results.append({"symbol": symbol, "fetched": len(candles), "stored": stored})
        await db.commit()

    return {"status": "ok", "timeframe": timeframe, "instruments": results}


@router.get("/live-prices")
async def get_live_prices(
    symbols: Optional[str] = Query(None, description="Comma-separated symbols. If empty, fetches all."),
    db: AsyncSession = Depends(get_db),
):
    """Get live prices for symbols from Fyers."""
    if not fyers_client.is_authenticated():
        return {"status": "error", "message": "Fyers not authenticated", "authenticated": False}

    if symbols:
        symbol_list = [s.strip() for s in symbols.split(",")]
    else:
        result = await db.execute(text("SELECT symbol FROM instruments WHERE is_active = true LIMIT 50"))
        rows = result.fetchall()
        symbol_list = [r[0] for r in rows]

    # Fyers allows max ~50 symbols per request, batch if needed
    all_prices = {}
    batch_size = 50
    for i in range(0, len(symbol_list), batch_size):
        batch = symbol_list[i:i + batch_size]
        prices = fyers_client.get_live_prices_batch(batch)
        all_prices.update(prices)

    # Also update approx_price in instruments table
    for sym, price_data in all_prices.items():
        try:
            await db.execute(text("""
                UPDATE instruments SET approx_price = :price WHERE symbol = :symbol
            """), {"price": price_data.get("ltp", 0), "symbol": sym})
        except Exception:
            pass
    await db.commit()

    return {"status": "ok", "prices": all_prices, "count": len(all_prices)}


@router.get("/stream")
async def stream_live_prices(
    symbols: Optional[str] = Query(None),
    interval: int = Query(3, description="Polling interval in seconds (min 2)"),
    db: AsyncSession = Depends(get_db),
):
    """Server-Sent Events (SSE) stream of live prices. Frontend connects once, gets continuous updates."""
    if not fyers_client.is_authenticated():
        return {"status": "error", "message": "Fyers not authenticated"}

    if symbols:
        symbol_list = [s.strip() for s in symbols.split(",")]
    else:
        result = await db.execute(text("SELECT symbol FROM instruments WHERE is_active = true LIMIT 50"))
        rows = result.fetchall()
        symbol_list = [r[0] for r in rows]

    interval = max(2, interval)  # Min 2 seconds to avoid API rate limits

    async def event_generator():
        try:
            while True:
                prices = fyers_client.get_live_prices_batch(symbol_list)
                if prices:
                    yield f"data: {json.dumps({'prices': prices, 'timestamp': datetime.now().isoformat()})}\n\n"
                else:
                    yield f"data: {json.dumps({'error': 'No data', 'timestamp': datetime.now().isoformat()})}\n\n"
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("SSE stream cancelled")
        except Exception as e:
            logger.error(f"SSE stream error: {e}")
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
