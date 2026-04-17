"""Live Trades API endpoints - real Fyers orders."""

import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app import fyers_client
from app.brokerage_calc import calc_brokerage
from app.day_limits_engine import day_limits_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/live", tags=["Live Trades"])

IST = timezone(timedelta(hours=5, minutes=30))


class LiveTradeCreate(BaseModel):
    symbol: str
    direction: Optional[str] = None  # LONG or SHORT
    side: Optional[str] = None  # BUY or SELL (frontend sends this)
    quantity: int = 1
    entry_price: float
    stop_loss: float
    target_price: Optional[float] = None
    target: Optional[float] = None  # frontend sends 'target'
    instrument_type: Optional[str] = None
    timeframe: Optional[str] = None
    strategy: Optional[str] = "MANUAL"
    signal_id: Optional[int] = None
    signal_score: Optional[float] = None
    signal_confidence: Optional[float] = None
    signal_reasons: Optional[list] = None
    indicators_snapshot: Optional[dict] = None

    @property
    def resolved_direction(self) -> str:
        if self.direction:
            return self.direction
        if self.side:
            return "LONG" if "BUY" in self.side.upper() else "SHORT"
        return "LONG"

    @property
    def resolved_target_price(self) -> float:
        return self.target_price or self.target or self.entry_price


@router.get("/trades")
async def get_live_trades(
    status: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Get all live trades."""
    query = "SELECT * FROM live_trades"
    params = {}
    if status:
        query += " WHERE status = :status"
        params["status"] = status
    query += " ORDER BY created_at DESC LIMIT 200"
    result = await db.execute(text(query), params)
    rows = result.mappings().all()
    return {"success": True, "trades": [dict(r) for r in rows]}


@router.get("/trades/open")
async def get_open_live_trades(db: AsyncSession = Depends(get_db)):
    """Get open live trades."""
    result = await db.execute(text(
        "SELECT * FROM live_trades WHERE status = 'OPEN' ORDER BY entry_time DESC"
    ))
    rows = result.mappings().all()
    return {"success": True, "trades": [dict(r) for r in rows]}


@router.get("/trades/{trade_id}")
async def get_live_trade(trade_id: int, db: AsyncSession = Depends(get_db)):
    """Get single live trade detail."""
    result = await db.execute(text(
        "SELECT * FROM live_trades WHERE id = :id"
    ), {"id": trade_id})
    row = result.mappings().first()
    if not row:
        return {"success": False, "error": "Trade not found"}
    return {"success": True, "trade": dict(row)}


@router.post("/trades")
async def create_live_trade(body: LiveTradeCreate, db: AsyncSession = Depends(get_db)):
    """Create a live trade (places real Fyers order)."""
    # Check day limits
    can_trade, reason = await day_limits_engine.check_before_trade(db, "LIVE")
    if not can_trade:
        return {"success": False, "reason": reason}

    # Generate trade ref
    now = datetime.now(IST)
    count_result = await db.execute(text(
        "SELECT COUNT(*) FROM live_trades WHERE DATE(created_at) = :today"
    ), {"today": now.date()})
    count = count_result.scalar() or 0
    trade_ref = f"LT-{now.strftime('%Y%m%d')}-{count + 1:04d}"

    direction = body.resolved_direction
    target_price = body.resolved_target_price

    display_sym = body.symbol.replace("NSE:", "").replace("-EQ", "")
    sl_pct = round(abs(body.entry_price - body.stop_loss) / body.entry_price * 100, 2)
    tgt_pct = round(abs(target_price - body.entry_price) / body.entry_price * 100, 2) if target_price else 0
    risk = abs(body.entry_price - body.stop_loss)
    reward = abs(target_price - body.entry_price) if target_price else 0
    rr = round(reward / risk, 2) if risk > 0 else 0

    # Place the real entry order on Fyers. We require an authenticated
    # session — otherwise we'd end up with a phantom OPEN row in live_trades
    # that has no corresponding broker order.
    if not fyers_client.is_authenticated():
        return {"success": False, "reason": "Fyers not connected. Please authenticate first."}

    fyers_side = 1 if direction == "LONG" else -1
    entry_order_resp = await fyers_client.place_order_async(
        symbol=body.symbol,
        side=fyers_side,
        qty=body.quantity,
        order_type=2,  # MARKET
        product_type="INTRADAY",
    )
    logger.info(f"Fyers entry order response ({trade_ref}): {entry_order_resp}")
    if not entry_order_resp or entry_order_resp.get("s") != "ok":
        err = entry_order_resp.get("message", "Unknown error") if entry_order_resp else "No response"
        code = entry_order_resp.get("code") if entry_order_resp else None
        hint = ""
        if code == -50 or "algo orders are not allowed" in (err or "").lower():
            hint = (
                " — Enable API/Algo trading for this app in Fyers "
                "(myaccount.fyers.in → My APIs → request algo activation)."
            )
        return {
            "success": False,
            "reason": f"Fyers entry order failed: {err}{hint}",
            "code": code,
        }

    fyers_order_id = entry_order_resp.get("id", "")

    # Insert trade record
    await db.execute(text("""
        INSERT INTO live_trades
        (trade_ref, symbol, display_symbol, direction, entry_price, entry_time,
         quantity, stop_loss, sl_percent, target_price, target_percent,
         strategy, risk_reward, signal_id, signal_score, status, fyers_order_id)
        VALUES (:trade_ref, :symbol, :display_sym, :direction, :entry_price, :entry_time,
                :quantity, :stop_loss, :sl_pct, :target_price, :tgt_pct,
                :strategy, :rr, :signal_id, :signal_score, 'OPEN', :order_id)
    """), {
        "trade_ref": trade_ref,
        "symbol": body.symbol,
        "display_sym": display_sym,
        "direction": direction,
        "entry_price": body.entry_price,
        "entry_time": now,
        "quantity": body.quantity,
        "stop_loss": body.stop_loss,
        "sl_pct": sl_pct,
        "target_price": target_price,
        "tgt_pct": tgt_pct,
        "strategy": body.strategy,
        "rr": rr,
        "signal_id": body.signal_id,
        "signal_score": body.signal_score,
        "order_id": fyers_order_id,
    })
    await db.commit()

    return {
        "success": True,
        "trade_ref": trade_ref,
        "mode": "LIVE",
        "message": "Live trade created",
    }


@router.put("/trades/{trade_id}/close")
async def close_live_trade(
    trade_id: int,
    exit_price: Optional[float] = Query(default=None),
    exit_reason: Optional[str] = Query(default="MANUAL"),
    db: AsyncSession = Depends(get_db),
):
    """Close a live trade."""
    result = await db.execute(text(
        "SELECT * FROM live_trades WHERE id = :id AND status = 'OPEN'"
    ), {"id": trade_id})
    trade = result.mappings().first()
    if not trade:
        return {"success": False, "error": "Open trade not found"}

    price = exit_price or trade["entry_price"]
    now = datetime.now(IST)

    # Place exit order on Fyers (opposite side of entry)
    if not fyers_client.is_authenticated():
        return {"success": False, "reason": "Fyers not connected. Please authenticate first."}

    exit_side = -1 if trade["direction"] == "LONG" else 1
    exit_order_resp = await fyers_client.place_order_async(
        symbol=trade["symbol"],
        side=exit_side,
        qty=trade["quantity"],
        order_type=2,  # MARKET
        product_type="INTRADAY",
    )
    logger.info(f"Fyers exit order response for trade {trade_id}: {exit_order_resp}")

    if not exit_order_resp or exit_order_resp.get("s") != "ok":
        err = exit_order_resp.get("message", "Unknown error") if exit_order_resp else "No response"
        return {"success": False, "reason": f"Fyers exit order failed: {err}"}

    fyers_exit_order_id = exit_order_resp.get("id", "")

    # Calculate P&L
    if trade["direction"] == "LONG":
        gross_pnl = (price - trade["entry_price"]) * trade["quantity"]
    else:
        gross_pnl = (trade["entry_price"] - price) * trade["quantity"]

    trade_value = price * trade["quantity"]
    charges = calc_brokerage(trade_value, trade["quantity"])
    net_pnl = gross_pnl - charges["total_charges"]

    duration = int((now - trade["entry_time"]).total_seconds() / 60) if trade["entry_time"] else 0

    await db.execute(text("""
        UPDATE live_trades SET
            status = 'CLOSED',
            exit_price = :exit_price,
            exit_time = :exit_time,
            exit_reason = :exit_reason,
            gross_pnl = :gross_pnl,
            brokerage = :brokerage,
            stt = :stt,
            exchange_charges = :exchange_charges,
            gst = :gst,
            sebi_charges = :sebi_charges,
            stamp_duty = :stamp_duty,
            net_pnl = :net_pnl,
            trade_duration_minutes = :duration
        WHERE id = :id
    """), {
        "exit_price": price,
        "exit_time": now,
        "exit_reason": exit_reason,
        "gross_pnl": round(gross_pnl, 2),
        "brokerage": charges["brokerage"],
        "stt": charges["stt"],
        "exchange_charges": charges["exchange_charges"],
        "gst": charges["gst"],
        "sebi_charges": charges["sebi_charges"],
        "stamp_duty": charges["stamp_duty"],
        "net_pnl": round(net_pnl, 2),
        "duration": duration,
        "id": trade_id,
    })
    await db.commit()

    return {
        "success": True,
        "trade_id": trade_id,
        "gross_pnl": round(gross_pnl, 2),
        "charges": charges,
        "net_pnl": round(net_pnl, 2),
        "fyers_exit_order_id": fyers_exit_order_id,
    }


@router.get("/funds")
async def get_live_fund_summary(db: AsyncSession = Depends(get_db)):
    """Get live Fyers fund data."""
    from app.routers.funds import get_live_funds
    return await get_live_funds(db)


@router.get("/analytics")
async def get_live_analytics(db: AsyncSession = Depends(get_db)):
    """Get live trading performance analytics."""
    result = await db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE status != 'OPEN') as total_trades,
            COUNT(*) FILTER (WHERE net_pnl > 0) as wins,
            COUNT(*) FILTER (WHERE net_pnl < 0) as losses,
            COUNT(*) FILTER (WHERE net_pnl = 0) as breakeven,
            COALESCE(SUM(net_pnl) FILTER (WHERE status != 'OPEN'), 0) as total_pnl,
            COALESCE(SUM(net_pnl) FILTER (WHERE net_pnl > 0), 0) as gross_wins,
            COALESCE(ABS(SUM(net_pnl) FILTER (WHERE net_pnl < 0)), 0) as gross_losses,
            COALESCE(AVG(net_pnl) FILTER (WHERE status != 'OPEN'), 0) as avg_pnl,
            COALESCE(SUM(brokerage + stt + exchange_charges + gst + sebi_charges + stamp_duty), 0) as total_charges
        FROM live_trades
    """))
    row = result.mappings().first()
    total = int(row["total_trades"]) if row else 0
    wins = int(row["wins"]) if row else 0
    gross_wins = float(row["gross_wins"]) if row else 0
    gross_losses = float(row["gross_losses"]) if row else 0

    return {
        "success": True,
        "mode": "LIVE",
        "total_trades": total,
        "wins": wins,
        "losses": int(row["losses"]) if row else 0,
        "breakeven": int(row["breakeven"]) if row else 0,
        "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
        "total_pnl": round(float(row["total_pnl"]), 2) if row else 0,
        "avg_pnl": round(float(row["avg_pnl"]), 2) if row else 0,
        "profit_factor": round(gross_wins / gross_losses, 2) if gross_losses > 0 else 0,
        "total_charges": round(float(row["total_charges"]), 2) if row else 0,
    }
