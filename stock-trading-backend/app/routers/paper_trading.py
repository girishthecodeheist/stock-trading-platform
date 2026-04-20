"""Paper Trading API router - simulate trades based on signals."""

import json
from datetime import datetime
from fastapi import APIRouter, Depends, Query, Body
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.database import get_db
from app.brokerage_calc import calc_brokerage
from app import audit

router = APIRouter(prefix="/api/paper-trades", tags=["paper-trading"])


@router.get("")
async def list_trades(
    status: Optional[str] = Query(None, description="OPEN or CLOSED"),
    symbol: Optional[str] = Query(None),
    limit: int = Query(100),
    db: AsyncSession = Depends(get_db),
):
    """List paper trades."""
    clauses = ["1=1"]
    params = {}

    if status:
        clauses.append("status = :status")
        params["status"] = status.upper()
    if symbol:
        clauses.append("symbol = :symbol")
        params["symbol"] = symbol

    where = " AND ".join(clauses)
    # Extended projection including product_type and charge breakdown so the
    # dashboard / Trade Journal can display net P&L without a second round
    # trip. Old rows with NULL new-columns coerce to 0.
    query = text(f"""
        SELECT id, symbol, instrument_type, timeframe, side, entry_price, entry_time,
               exit_price, exit_time, quantity, stop_loss, target, status, result,
               pnl_percent, pnl_amount, signal_confidence, signal_reasons,
               indicators_snapshot, duration_minutes, exit_reason,
               COALESCE(is_auto_trade, false) as is_auto_trade,
               COALESCE(product_type, 'INTRADAY') as product_type,
               COALESCE(brokerage, 0) as brokerage,
               COALESCE(stt, 0) as stt,
               COALESCE(exchange_charges, 0) as exchange_charges,
               COALESCE(gst, 0) as gst,
               COALESCE(sebi_charges, 0) as sebi_charges,
               COALESCE(stamp_duty, 0) as stamp_duty,
               gross_pnl, net_pnl
        FROM paper_trades
        WHERE {where}
        ORDER BY entry_time DESC
        LIMIT :limit
    """)
    params["limit"] = limit

    result = await db.execute(query, params)
    rows = result.fetchall()

    return [
        {
            "id": r[0], "symbol": r[1], "instrument_type": r[2],
            "timeframe": r[3], "side": r[4],
            "entry_price": float(r[5]) if r[5] else None,
            "entry_time": str(r[6]) if r[6] else None,
            "exit_price": float(r[7]) if r[7] else None,
            "exit_time": str(r[8]) if r[8] else None,
            "quantity": r[9],
            "stop_loss": float(r[10]) if r[10] else None,
            "target": float(r[11]) if r[11] else None,
            "status": r[12], "result": r[13],
            "pnl_percent": float(r[14]) if r[14] else None,
            "pnl_amount": float(r[15]) if r[15] else None,
            "signal_confidence": float(r[16]) if r[16] else None,
            "signal_reasons": r[17],
            "indicators_snapshot": r[18],
            "duration_minutes": r[19],
            "exit_reason": r[20],
            "is_auto_trade": bool(r[21]) if r[21] is not None else False,
            "product_type": r[22],
            "brokerage": float(r[23]) if r[23] is not None else 0.0,
            "stt": float(r[24]) if r[24] is not None else 0.0,
            "exchange_charges": float(r[25]) if r[25] is not None else 0.0,
            "gst": float(r[26]) if r[26] is not None else 0.0,
            "sebi_charges": float(r[27]) if r[27] is not None else 0.0,
            "stamp_duty": float(r[28]) if r[28] is not None else 0.0,
            "gross_pnl": float(r[29]) if r[29] is not None else None,
            "net_pnl": float(r[30]) if r[30] is not None else None,
        }
        for r in rows
    ]


@router.post("")
async def create_trade(
    trade: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """Create a new paper trade.

    Product type resolution matches the live path (``POST /api/v1/live/trades``)
    so a PAPER trade placed from the UI incurs the same MIS/CNC charges as
    its LIVE equivalent would. When the request body doesn't pin a
    ``product_type`` we fall back to ``trading_settings.product_type``
    (INTRADAY/CNC) instead of the old hard-coded INTRADAY default — that
    way the settings dropdown actually controls paper too.
    """
    # ``trade`` is untyped so the incoming value can be anything (None, int,
    # list, …). Cast through ``str`` before ``.upper()`` so a malformed
    # payload returns 400-ish behaviour (falls through to the settings
    # lookup / default) instead of raising AttributeError.
    raw_pt = trade.get("product_type")
    product_type = str(raw_pt).upper() if raw_pt else ""
    if not product_type:
        try:
            pt_row = await db.execute(text(
                "SELECT product_type FROM trading_settings ORDER BY id ASC LIMIT 1"
            ))
            pt_val = pt_row.scalar()
            if pt_val:
                product_type = str(pt_val).upper()
        except Exception:
            # A failed SELECT leaves the async session in an aborted state
            # on PostgreSQL; every subsequent statement (including the
            # INSERT below) would then fail with InFailedSqlTransactionError.
            # Rolling back resets the session so create_trade degrades to
            # the INTRADAY default instead of breaking paper placement
            # entirely.
            await db.rollback()
    # Normalise DELIVERY → CNC so stored values stay in sync with the
    # settings table / Fyers product-type vocabulary (trading_settings
    # only ever stores "INTRADAY" or "CNC"). brokerage_calc treats both
    # spellings as the same rate card, but downstream callers that
    # compare ``== "CNC"`` would otherwise miss a DELIVERY-stored row.
    if product_type == "DELIVERY":
        product_type = "CNC"
    if product_type not in ("INTRADAY", "CNC"):
        product_type = "INTRADAY"
    query = text("""
        INSERT INTO paper_trades (symbol, instrument_type, timeframe, side, entry_price,
            entry_time, quantity, stop_loss, target, status, signal_confidence,
            signal_reasons, indicators_snapshot, product_type)
        VALUES (:symbol, :instrument_type, :timeframe, :side, :entry_price,
            :entry_time, :quantity, :stop_loss, :target, 'OPEN', :signal_confidence,
            :signal_reasons, :indicators_snapshot, :product_type)
        RETURNING id
    """)

    result = await db.execute(query, {
        "symbol": trade["symbol"],
        "instrument_type": trade.get("instrument_type", "EQUITY"),
        "timeframe": trade.get("timeframe", "1D"),
        "side": trade.get("side", "BUY"),
        "entry_price": trade["entry_price"],
        "entry_time": datetime.utcnow(),
        "quantity": trade.get("quantity", 1),
        "stop_loss": trade.get("stop_loss"),
        "target": trade.get("target"),
        "signal_confidence": trade.get("signal_confidence"),
        "signal_reasons": json.dumps(trade.get("signal_reasons", [])),
        "indicators_snapshot": json.dumps(trade.get("indicators_snapshot", {})),
        "product_type": product_type,
    })
    await db.commit()

    trade_id = result.scalar()

    await audit.log_event(
        trade_id=trade_id,
        trade_type="PAPER",
        event_type=audit.EVENT_TRADE_PLACED,
        symbol=trade["symbol"],
        new_value={
            "entry_price": trade["entry_price"],
            "stop_loss": trade.get("stop_loss"),
            "target": trade.get("target"),
            "quantity": trade.get("quantity", 1),
            "side": trade.get("side", "BUY"),
            "product_type": product_type,
        },
        reason="Paper trade created",
        trigger_data={
            "signal_confidence": trade.get("signal_confidence"),
            "signal_reasons": trade.get("signal_reasons", []),
        },
    )

    return {"id": trade_id, "message": "Paper trade created", "status": "OPEN"}


@router.post("/{trade_id}/close")
async def close_trade(
    trade_id: int,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """Close an open paper trade with charges breakdown + audit trail."""
    exit_price = body.get("exit_price")
    exit_reason = body.get("exit_reason", "Manual close")

    trade_q = text(
        "SELECT entry_price, side, entry_time, quantity, symbol, "
        "       COALESCE(product_type, 'INTRADAY') "
        "  FROM paper_trades WHERE id = :id AND status = 'OPEN'"
    )
    result = await db.execute(trade_q, {"id": trade_id})
    row = result.fetchone()

    if not row:
        return {"error": "Trade not found or already closed"}

    entry_price, side, entry_time, quantity, symbol, product_type = row
    quantity = quantity or 1

    if side == "BUY":
        pnl_pct = (exit_price - entry_price) / entry_price * 100
        buy_price, sell_price = float(entry_price), float(exit_price)
    else:
        pnl_pct = (entry_price - exit_price) / entry_price * 100
        # Short: we "sell" at entry and "buy" back at exit. Fyers still
        # charges STT on the sell leg and stamp duty on the buy leg.
        buy_price, sell_price = float(exit_price), float(entry_price)

    gross_pnl = (
        (exit_price - entry_price) * quantity
        if side == "BUY"
        else (entry_price - exit_price) * quantity
    )
    result_str = "WIN" if pnl_pct > 0 else ("LOSS" if pnl_pct < 0 else "BREAKEVEN")

    buy_value = buy_price * quantity
    sell_value = sell_price * quantity
    charges = calc_brokerage(buy_value, sell_value, quantity, product_type=product_type)
    total_charges = float(charges["total_charges"])
    net_pnl = round(float(gross_pnl) - total_charges, 2)

    now = datetime.utcnow()
    duration = int((now - entry_time).total_seconds() / 60) if entry_time else None

    update_q = text("""
        UPDATE paper_trades SET
            exit_price = :exit_price, exit_time = :exit_time,
            status = 'CLOSED', result = :result,
            pnl_percent = :pnl_pct, pnl_amount = :pnl_amount,
            duration_minutes = :duration, exit_reason = :exit_reason,
            brokerage = :brokerage, stt = :stt, exchange_charges = :exchange,
            gst = :gst, sebi_charges = :sebi, stamp_duty = :stamp,
            gross_pnl = :gross_pnl, net_pnl = :net_pnl,
            product_type = :product_type
        WHERE id = :id
    """)
    await db.execute(update_q, {
        "exit_price": exit_price,
        "exit_time": now,
        "result": result_str,
        "pnl_pct": round(pnl_pct, 2),
        "pnl_amount": round(float(gross_pnl), 2),
        "duration": duration,
        "exit_reason": exit_reason,
        "brokerage": charges["brokerage"],
        "stt": charges["stt"],
        "exchange": charges["exchange_charges"],
        "gst": charges["gst"],
        "sebi": charges["sebi_charges"],
        "stamp": charges["stamp_duty"],
        "gross_pnl": round(float(gross_pnl), 2),
        "net_pnl": net_pnl,
        "product_type": product_type,
        "id": trade_id,
    })
    await db.commit()

    await audit.log_event(
        trade_id=trade_id,
        trade_type="PAPER",
        event_type=audit.EVENT_TRADE_CLOSED,
        symbol=symbol,
        old_value={"status": "OPEN"},
        new_value={
            "exit_price": exit_price,
            "result": result_str,
            "gross_pnl": round(float(gross_pnl), 2),
            "net_pnl": net_pnl,
            "total_charges": total_charges,
            "duration_minutes": duration,
        },
        reason=exit_reason,
        trigger_data={"charges": charges, "product_type": product_type},
    )

    return {
        "id": trade_id,
        "result": result_str,
        "pnl_percent": round(pnl_pct, 2),
        "pnl_amount": round(float(gross_pnl), 2),
        "gross_pnl": round(float(gross_pnl), 2),
        "net_pnl": net_pnl,
        "total_charges": total_charges,
        "charges": charges,
        "exit_reason": exit_reason,
    }


@router.get("/{trade_id}/audit")
async def paper_trade_audit(
    trade_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Full audit trail for a single paper trade, oldest first."""
    q = text(
        "SELECT id, event_type, symbol, timestamp, old_value, new_value, "
        "       reason, trigger_data, extra_metadata "
        "  FROM trade_audit_log "
        " WHERE trade_id = :id AND trade_type = 'PAPER' "
        " ORDER BY timestamp ASC, id ASC"
    )
    rows = (await db.execute(q, {"id": trade_id})).fetchall()
    return [
        {
            "id": r[0],
            "event_type": r[1],
            "symbol": r[2],
            "timestamp": str(r[3]) if r[3] else None,
            "old_value": r[4],
            "new_value": r[5],
            "reason": r[6],
            "trigger_data": r[7],
            "metadata": r[8],
        }
        for r in rows
    ]


@router.get("/analytics")
async def get_analytics(
    date_from: Optional[str] = Query(None, description="Filter from date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Filter to date (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
):
    """Get paper trading performance analytics with optional date filter."""
    # Build date filter clause
    date_clauses = []
    date_params = {}
    if date_from:
        date_clauses.append("entry_time >= :date_from")
        date_params["date_from"] = date_from
    if date_to:
        date_clauses.append("entry_time <= :date_to::date + interval '1 day'")
        date_params["date_to"] = date_to
    date_where = (" AND " + " AND ".join(date_clauses)) if date_clauses else ""

    # Total trades
    total_q = text(f"SELECT COUNT(*) FROM paper_trades WHERE 1=1{date_where}")
    total_result = await db.execute(total_q, date_params)
    total_trades = total_result.scalar() or 0

    # Open trades
    open_q = text(f"SELECT COUNT(*) FROM paper_trades WHERE status = 'OPEN'{date_where}")
    open_result = await db.execute(open_q, date_params)
    open_trades = open_result.scalar() or 0

    # Closed trades stats
    closed_q = text(f"""
        SELECT 
            COUNT(*) as total_closed,
            SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN result = 'LOSS' THEN 1 ELSE 0 END) as losses,
            SUM(CASE WHEN result = 'BREAKEVEN' THEN 1 ELSE 0 END) as breakeven,
            AVG(CASE WHEN result = 'WIN' THEN pnl_percent ELSE NULL END) as avg_win,
            AVG(CASE WHEN result = 'LOSS' THEN pnl_percent ELSE NULL END) as avg_loss,
            AVG(pnl_percent) as avg_pnl,
            SUM(pnl_amount) as total_pnl,
            AVG(duration_minutes) as avg_duration,
            AVG(signal_confidence) as avg_confidence
        FROM paper_trades WHERE status = 'CLOSED'{date_where}
    """)
    closed_result = await db.execute(closed_q, date_params)
    stats = closed_result.fetchone()

    total_closed = stats[0] or 0
    wins = stats[1] or 0
    losses = stats[2] or 0
    breakeven = stats[3] or 0
    avg_win = float(stats[4]) if stats[4] else 0
    avg_loss = float(stats[5]) if stats[5] else 0
    avg_pnl = float(stats[6]) if stats[6] else 0
    total_pnl = float(stats[7]) if stats[7] else 0
    avg_duration = float(stats[8]) if stats[8] else 0
    avg_confidence = float(stats[9]) if stats[9] else 0

    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0

    # Strong buy accuracy
    strong_q = text("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins
        FROM paper_trades 
        WHERE status = 'CLOSED' AND signal_confidence >= 60
    """)
    strong_result = await db.execute(strong_q)
    strong_stats = strong_result.fetchone()
    strong_total = strong_stats[0] or 0
    strong_wins = strong_stats[1] or 0
    strong_accuracy = (strong_wins / strong_total * 100) if strong_total > 0 else 0

    # Recent performance (last 10 trades)
    recent_q = text("""
        SELECT symbol, side, entry_price, exit_price, pnl_percent, result, 
               duration_minutes, exit_reason, entry_time
        FROM paper_trades
        WHERE status = 'CLOSED'
        ORDER BY exit_time DESC
        LIMIT 10
    """)
    recent_result = await db.execute(recent_q)
    recent_rows = recent_result.fetchall()
    recent_trades = [
        {
            "symbol": r[0], "side": r[1],
            "entry_price": float(r[2]) if r[2] else None,
            "exit_price": float(r[3]) if r[3] else None,
            "pnl_percent": float(r[4]) if r[4] else None,
            "result": r[5], "duration_minutes": r[6],
            "exit_reason": r[7],
            "entry_time": str(r[8]) if r[8] else None,
        }
        for r in recent_rows
    ]

    return {
        "total_trades": total_trades,
        "open_trades": open_trades,
        "closed_trades": total_closed,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": round(win_rate, 1),
        "avg_profit": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "avg_pnl": round(avg_pnl, 2),
        "total_pnl": round(total_pnl, 2),
        "avg_duration_minutes": round(avg_duration, 0),
        "avg_confidence": round(avg_confidence, 1),
        "strong_signal_accuracy": round(strong_accuracy, 1),
        "strong_signal_total": strong_total,
        "recent_trades": recent_trades,
    }
