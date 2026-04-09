"""Heatmap API endpoints."""

import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.heatmap_poller import heatmap_poller
from app import fyers_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/heatmap", tags=["Heatmap"])


@router.get("/live")
async def get_live_heatmap(db: AsyncSession = Depends(get_db)):
    """Get current heatmap with all sectors + movers."""
    all_stocks = heatmap_poller.get_all_stocks()
    sectors = heatmap_poller.get_sectors()
    top_gainers = heatmap_poller.get_top_gainers(10)
    top_losers = heatmap_poller.get_top_losers(10)

    return {
        "success": True,
        "total_stocks": len(all_stocks),
        "last_poll": heatmap_poller.last_poll_time.isoformat() if heatmap_poller.last_poll_time else None,
        "sectors": sectors,
        "top_gainers": top_gainers,
        "top_losers": top_losers,
        "all_stocks": all_stocks,
    }


@router.get("/sectors")
async def get_sectors():
    """Get sector summary."""
    return {
        "success": True,
        "sectors": heatmap_poller.get_sectors(),
    }


@router.get("/top-movers")
async def get_top_movers(
    n: int = Query(default=20, ge=1, le=200),
    min_volume: int = Query(default=100000),
    min_change_pct: float = Query(default=0.5),
):
    """Get top N movers by absolute change %."""
    movers = heatmap_poller.get_top_movers(n=n, min_volume=min_volume, min_change_pct=min_change_pct)
    return {
        "success": True,
        "count": len(movers),
        "movers": movers,
    }


@router.post("/force-refresh")
async def force_refresh(db: AsyncSession = Depends(get_db)):
    """Manually trigger heatmap poll."""
    try:
        result = await heatmap_poller.poll_heatmap()

        # Persist to DB
        all_stocks = heatmap_poller.get_all_stocks()
        for i, s in enumerate(all_stocks):
            await db.execute(text("""
                INSERT INTO nse_heatmap_snapshots
                (symbol, display_sym, sector, ltp, change_pct, volume,
                 open_price, high_price, low_price, prev_close,
                 is_top_mover, rank_by_move, fetched_at)
                VALUES (:symbol, :display_sym, :sector, :ltp, :change_pct, :volume,
                        :open_price, :high_price, :low_price, :prev_close,
                        :is_top_mover, :rank, :fetched_at)
            """), {
                "symbol": s["symbol"],
                "display_sym": s["display_symbol"],
                "sector": s["sector"],
                "ltp": s["ltp"],
                "change_pct": s["change_pct"],
                "volume": s["volume"],
                "open_price": s.get("open_price", 0),
                "high_price": s.get("high_price", 0),
                "low_price": s.get("low_price", 0),
                "prev_close": s.get("prev_close", 0),
                "is_top_mover": abs(s["change_pct"]) >= 2.0,
                "rank": i + 1,
                "fetched_at": datetime.utcnow(),
            })
        await db.commit()

        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Heatmap force refresh failed: {e}")
        return {"success": False, "error": str(e)}


@router.get("/history")
async def get_heatmap_history(
    date: str = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Get historical heatmap snapshot from DB."""
    query_date = date or datetime.utcnow().strftime("%Y-%m-%d")
    result = await db.execute(text("""
        SELECT * FROM nse_heatmap_snapshots
        WHERE DATE(fetched_at) = :date
        ORDER BY rank_by_move ASC
        LIMIT 200
    """), {"date": query_date})
    rows = result.mappings().all()
    return {
        "success": True,
        "date": query_date,
        "count": len(rows),
        "stocks": [dict(r) for r in rows],
    }


@router.get("/backdate")
async def get_backdate_simulation(
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    db: AsyncSession = Depends(get_db),
):
    """Load historical data for a past date for simulation.

    This fetches OHLCV data from Fyers for the given date and builds
    a simulated heatmap showing what stocks were doing on that day.
    Useful for weekends/holidays to verify paper trading logic.
    """
    try:
        target_date = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return {"success": False, "error": "Invalid date format. Use YYYY-MM-DD"}

    # First check if we have stored snapshots for this date
    result = await db.execute(text("""
        SELECT * FROM nse_heatmap_snapshots
        WHERE DATE(fetched_at) = :date
        ORDER BY rank_by_move ASC
        LIMIT 500
    """), {"date": date})
    existing_rows = result.mappings().all()

    if existing_rows:
        # Build sectors from stored data
        stocks = [dict(r) for r in existing_rows]
        sectors = _build_sectors_from_stocks(stocks)
        gainers = sorted([s for s in stocks if s.get("change_pct", 0) > 0],
                         key=lambda x: x.get("change_pct", 0), reverse=True)[:10]
        losers = sorted([s for s in stocks if s.get("change_pct", 0) < 0],
                        key=lambda x: x.get("change_pct", 0))[:10]
        return {
            "success": True,
            "date": date,
            "source": "database",
            "total_stocks": len(stocks),
            "sectors": sectors,
            "top_gainers": gainers,
            "top_losers": losers,
            "all_stocks": stocks,
        }

    # If no stored data, try to build from Fyers historical OHLCV
    if not fyers_client.is_authenticated():
        return {
            "success": False,
            "error": "No stored data for this date and Fyers not connected. "
                     "Connect Fyers or choose a date when heatmap was captured.",
            "date": date,
        }

    # Get list of instruments to fetch
    inst_result = await db.execute(text("""
        SELECT symbol, name, segment FROM instruments
        WHERE segment = 'EQUITY' AND is_active = true
        LIMIT 200
    """))
    instruments = inst_result.mappings().all()

    if not instruments:
        return {"success": False, "error": "No instruments found in database"}

    # Fetch 1D candle for each instrument on the target date
    next_day = (target_date + timedelta(days=1)).strftime("%Y-%m-%d")
    simulated_stocks = []

    for inst in instruments:
        try:
            data = {
                "symbol": inst["symbol"],
                "resolution": "1D",
                "date_format": "1",
                "range_from": date,
                "range_to": next_day,
                "cont_flag": "1",
            }
            fm = fyers_client.get_fyers_model()
            if not fm:
                continue
            response = fm.history(data)
            candles = response.get("candles", [])
            if not candles:
                continue

            # Use the last candle of the day
            c = candles[-1]
            open_price = float(c[1])
            close_price = float(c[4])
            change_pct = round((close_price - open_price) / open_price * 100, 2) if open_price > 0 else 0

            display_sym = inst["symbol"].replace("NSE:", "").replace("-EQ", "")
            stock = {
                "symbol": inst["symbol"],
                "display_sym": display_sym,
                "display_symbol": display_sym,
                "sector": "EQUITY",
                "ltp": close_price,
                "change_pct": change_pct,
                "volume": int(c[5]),
                "open_price": open_price,
                "high_price": float(c[2]),
                "low_price": float(c[3]),
                "prev_close": open_price,
            }
            simulated_stocks.append(stock)
        except Exception as e:
            logger.debug(f"Skip {inst['symbol']}: {e}")
            continue

    if not simulated_stocks:
        return {"success": False, "error": f"No data available for {date}. Market may have been closed."}

    # Sort and build sectors
    simulated_stocks.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    sectors = _build_sectors_from_stocks(simulated_stocks)
    gainers = sorted([s for s in simulated_stocks if s["change_pct"] > 0],
                     key=lambda x: x["change_pct"], reverse=True)[:10]
    losers = sorted([s for s in simulated_stocks if s["change_pct"] < 0],
                    key=lambda x: x["change_pct"])[:10]

    # Persist to DB for future queries
    for i, s in enumerate(simulated_stocks):
        try:
            await db.execute(text("""
                INSERT INTO nse_heatmap_snapshots
                (symbol, display_sym, sector, ltp, change_pct, volume,
                 open_price, high_price, low_price, prev_close,
                 is_top_mover, rank_by_move, fetched_at)
                VALUES (:symbol, :display_sym, :sector, :ltp, :change_pct, :volume,
                        :open_price, :high_price, :low_price, :prev_close,
                        :is_top_mover, :rank, :fetched_at)
            """), {
                "symbol": s["symbol"],
                "display_sym": s.get("display_sym", s.get("display_symbol", "")),
                "sector": s.get("sector", "EQUITY"),
                "ltp": s["ltp"],
                "change_pct": s["change_pct"],
                "volume": s.get("volume", 0),
                "open_price": s.get("open_price", 0),
                "high_price": s.get("high_price", 0),
                "low_price": s.get("low_price", 0),
                "prev_close": s.get("prev_close", 0),
                "is_top_mover": abs(s["change_pct"]) >= 2.0,
                "rank": i + 1,
                "fetched_at": target_date,
            })
        except Exception:
            pass
    await db.commit()

    return {
        "success": True,
        "date": date,
        "source": "fyers_historical",
        "total_stocks": len(simulated_stocks),
        "sectors": sectors,
        "top_gainers": gainers,
        "top_losers": losers,
        "all_stocks": simulated_stocks,
    }


def _build_sectors_from_stocks(stocks: list) -> list:
    """Build sector summaries from a list of stocks."""
    by_sector = {}
    for s in stocks:
        sector = s.get("sector", "EQUITY")
        if sector not in by_sector:
            by_sector[sector] = []
        by_sector[sector].append(s)

    sectors = []
    for name, sector_stocks in by_sector.items():
        avg_change = sum(s.get("change_pct", 0) for s in sector_stocks) / len(sector_stocks) if sector_stocks else 0
        sectors.append({
            "name": name,
            "avg_change": round(avg_change, 2),
            "sector_bias": "BULLISH" if avg_change > 0 else "BEARISH",
            "stock_count": len(sector_stocks),
        })
    return sectors
