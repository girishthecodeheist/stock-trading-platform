"""Paper Trading API router - simulate trades based on signals."""

import json
from datetime import datetime
from fastapi import APIRouter, Depends, Query, Body
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.database import get_db

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
    query = text(f"""
        SELECT id, symbol, instrument_type, timeframe, side, entry_price, entry_time,
               exit_price, exit_time, quantity, stop_loss, target, status, result,
               pnl_percent, pnl_amount, signal_confidence, signal_reasons,
               indicators_snapshot, duration_minutes, exit_reason,
               COALESCE(is_auto_trade, false) as is_auto_trade
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
        }
        for r in rows
    ]


@router.post("")
async def create_trade(
    trade: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """Create a new paper trade."""
    query = text("""
        INSERT INTO paper_trades (symbol, instrument_type, timeframe, side, entry_price,
            entry_time, quantity, stop_loss, target, status, signal_confidence,
            signal_reasons, indicators_snapshot)
        VALUES (:symbol, :instrument_type, :timeframe, :side, :entry_price,
            :entry_time, :quantity, :stop_loss, :target, 'OPEN', :signal_confidence,
            :signal_reasons, :indicators_snapshot)
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
    })
    await db.commit()
    
    trade_id = result.scalar()
    return {"id": trade_id, "message": "Paper trade created", "status": "OPEN"}


@router.post("/{trade_id}/close")
async def close_trade(
    trade_id: int,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """Close an open paper trade."""
    exit_price = body.get("exit_price")
    exit_reason = body.get("exit_reason", "Manual close")

    # Get the trade
    trade_q = text("SELECT entry_price, side, entry_time, quantity FROM paper_trades WHERE id = :id AND status = 'OPEN'")
    result = await db.execute(trade_q, {"id": trade_id})
    row = result.fetchone()

    if not row:
        return {"error": "Trade not found or already closed"}

    entry_price, side, entry_time, quantity = row
    quantity = quantity or 1
    
    # Calculate PnL
    if side == "BUY":
        pnl_pct = (exit_price - entry_price) / entry_price * 100
    else:
        pnl_pct = (entry_price - exit_price) / entry_price * 100
    
    # P&L amount = per-share P&L * quantity
    pnl_amount = (exit_price - entry_price) * quantity if side == "BUY" else (entry_price - exit_price) * quantity
    result_str = "WIN" if pnl_pct > 0 else ("LOSS" if pnl_pct < 0 else "BREAKEVEN")
    
    now = datetime.utcnow()
    duration = int((now - entry_time).total_seconds() / 60) if entry_time else None

    update_q = text("""
        UPDATE paper_trades SET
            exit_price = :exit_price, exit_time = :exit_time,
            status = 'CLOSED', result = :result,
            pnl_percent = :pnl_pct, pnl_amount = :pnl_amount,
            duration_minutes = :duration, exit_reason = :exit_reason
        WHERE id = :id
    """)
    await db.execute(update_q, {
        "exit_price": exit_price,
        "exit_time": now,
        "result": result_str,
        "pnl_pct": round(pnl_pct, 2),
        "pnl_amount": round(pnl_amount, 2),
        "duration": duration,
        "exit_reason": exit_reason,
        "id": trade_id,
    })
    await db.commit()

    return {
        "id": trade_id,
        "result": result_str,
        "pnl_percent": round(pnl_pct, 2),
        "pnl_amount": round(pnl_amount, 2),
        "exit_reason": exit_reason,
    }


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
