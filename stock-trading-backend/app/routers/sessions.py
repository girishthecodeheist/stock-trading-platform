"""Trading sessions router.

A "trading session" is a named bucket of paper-trading capital. At most
one session is ``ACTIVE`` at any time — all new paper trades, the
auto-trade engine's margin/exposure/daily-count queries, and the
``/funds/paper`` summary scope themselves to that session.

Starting a new session:
  * Closes any previously-ACTIVE session (squaring off every open paper
    trade at its entry price, rolling up P&L, stamping
    ``closed_at / closing_capital / total_pnl / total_trades /
    win_count / loss_count``).
  * Inserts the new row as ``ACTIVE`` with the declared
    ``starting_capital``.
  * Overwrites ``trading_settings.simulated_capital`` so the auto-trade
    engine uses the new capital as the starting cash.
  * Resets the auto-trade engine's in-memory counters
    (``_trades_placed_today``, ``_daily_target_met``, ``_auto_trade_log``)
    so daily gates don't spill over.
  * Writes a ``SESSION_STARTED`` row into ``trade_audit_log`` tagged with
    the new session's id.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app import audit, auto_trade_engine


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/sessions", tags=["Sessions"])


EVENT_SESSION_STARTED = "SESSION_STARTED"
EVENT_SESSION_CLOSED = "SESSION_CLOSED"


# --- Helpers ----------------------------------------------------------------


async def _get_active_session_row(db: AsyncSession) -> Optional[dict]:
    row = (await db.execute(text(
        "SELECT id, session_name, starting_capital, status, created_at, "
        "       closed_at, closing_capital, total_pnl, total_trades, "
        "       win_count, loss_count, notes "
        "  FROM trading_sessions "
        " WHERE status = 'ACTIVE' "
        " ORDER BY created_at DESC LIMIT 1"
    ))).mappings().first()
    return dict(row) if row else None


async def _session_summary(db: AsyncSession, session_id: int) -> Dict[str, Any]:
    """Roll up P&L + trade counts for a session. Works for ACTIVE or CLOSED.

    Uses ``pnl_amount`` (gross) as the primary P&L figure for parity with
    the funds endpoint and the engine's internal margin math.
    """
    summary = (await db.execute(text(
        "SELECT COUNT(*)                                             AS total_trades, "
        "       COUNT(*) FILTER (WHERE status = 'OPEN')              AS open_trades, "
        "       COUNT(*) FILTER (WHERE status = 'CLOSED')            AS closed_trades, "
        "       COUNT(*) FILTER (WHERE result = 'WIN')               AS win_count, "
        "       COUNT(*) FILTER (WHERE result = 'LOSS')              AS loss_count, "
        "       COALESCE(SUM(pnl_amount), 0)                          AS total_pnl, "
        "       COALESCE(SUM(COALESCE(net_pnl, pnl_amount)), 0)      AS total_net_pnl, "
        "       COALESCE(SUM(entry_price * quantity) "
        "                FILTER (WHERE status = 'OPEN'), 0)          AS open_exposure "
        "  FROM paper_trades WHERE session_id = :sid"
    ), {"sid": session_id})).mappings().first() or {}

    return {
        "total_trades": int(summary.get("total_trades") or 0),
        "open_trades": int(summary.get("open_trades") or 0),
        "closed_trades": int(summary.get("closed_trades") or 0),
        "win_count": int(summary.get("win_count") or 0),
        "loss_count": int(summary.get("loss_count") or 0),
        "total_pnl": float(summary.get("total_pnl") or 0),
        "total_net_pnl": float(summary.get("total_net_pnl") or 0),
        "open_exposure": float(summary.get("open_exposure") or 0),
    }


def _row_to_dict(row) -> Dict[str, Any]:
    r = dict(row)
    for key in ("created_at", "closed_at"):
        if r.get(key) is not None:
            r[key] = str(r[key])
    return r


async def _close_session(
    db: AsyncSession, session: dict, reason: str = "Closed by user",
) -> dict:
    """Square off all open paper trades in a session and stamp it CLOSED.

    Open trades are marked CLOSED with ``exit_price = entry_price`` (no
    P&L impact — we don't have a live price at this point) and an
    ``exit_reason`` of 'SESSION_CLOSED'. Summary columns are then written
    onto the session row itself so the closed session view doesn't have to
    re-aggregate.
    """
    sid = int(session["id"])

    # Square off open trades at entry price so session math stays consistent
    # and the active session's exposure goes to zero immediately.
    open_rows = (await db.execute(text(
        "SELECT id, entry_price, entry_time, quantity, symbol, side "
        "  FROM paper_trades "
        " WHERE session_id = :sid AND status = 'OPEN'"
    ), {"sid": sid})).fetchall()

    now = datetime.utcnow()
    for r in open_rows:
        trade_id = r[0]
        entry_price = float(r[1])
        await db.execute(text(
            "UPDATE paper_trades "
            "   SET status = 'CLOSED', exit_price = :price, exit_time = :ts, "
            "       result = 'BREAKEVEN', pnl_percent = 0, pnl_amount = 0, "
            "       gross_pnl = 0, net_pnl = 0, "
            "       exit_reason = 'SESSION_CLOSED' "
            " WHERE id = :id"
        ), {"price": entry_price, "ts": now, "id": trade_id})
    if open_rows:
        await db.commit()

    summary = await _session_summary(db, sid)
    capital = float(session.get("starting_capital") or 0)
    closing_capital = capital + summary["total_pnl"]

    await db.execute(text(
        "UPDATE trading_sessions "
        "   SET status = 'CLOSED', "
        "       closed_at = :closed_at, "
        "       closing_capital = :closing_capital, "
        "       total_pnl = :total_pnl, "
        "       total_trades = :total_trades, "
        "       win_count = :win_count, "
        "       loss_count = :loss_count "
        " WHERE id = :id"
    ), {
        "closed_at": now,
        "closing_capital": round(closing_capital, 2),
        "total_pnl": round(summary["total_pnl"], 2),
        "total_trades": summary["total_trades"],
        "win_count": summary["win_count"],
        "loss_count": summary["loss_count"],
        "id": sid,
    })
    await db.commit()

    try:
        await audit.log_event(
            trade_id=0,
            trade_type="SESSION",
            event_type=EVENT_SESSION_CLOSED,
            symbol=None,
            old_value={"status": "ACTIVE"},
            new_value={
                "status": "CLOSED",
                "closing_capital": round(closing_capital, 2),
                "total_pnl": round(summary["total_pnl"], 2),
                "total_trades": summary["total_trades"],
                "win_count": summary["win_count"],
                "loss_count": summary["loss_count"],
            },
            reason=reason,
            extra_metadata={"session_id": sid, "squared_off": len(open_rows)},
        )
        # Backfill session_id on the session-close audit row so the audit
        # endpoint can retrieve it cleanly by id.
        await db.execute(text(
            "UPDATE trade_audit_log SET session_id = :sid "
            " WHERE trade_type = 'SESSION' AND event_type = :evt "
            "   AND extra_metadata::text LIKE :marker"
        ), {
            "sid": sid,
            "evt": EVENT_SESSION_CLOSED,
            "marker": f'%"session_id": {sid}%',
        })
        await db.commit()
    except Exception as e:
        logger.debug(f"Audit log for SESSION_CLOSED failed: {e}")

    return {
        "id": sid,
        "session_name": session.get("session_name"),
        "status": "CLOSED",
        "closing_capital": round(closing_capital, 2),
        "total_pnl": round(summary["total_pnl"], 2),
        "total_trades": summary["total_trades"],
        "win_count": summary["win_count"],
        "loss_count": summary["loss_count"],
        "squared_off_open_trades": len(open_rows),
    }


def _reset_engine_daily_state() -> None:
    """Reset in-memory engine counters after starting a new session."""
    auto_trade_engine._trades_placed_today = 0
    auto_trade_engine._daily_target_met = False
    auto_trade_engine._auto_trade_log = []
    auto_trade_engine._last_trade_time_per_symbol = {}
    auto_trade_engine._rejected_signals = []
    # Flush the settings cache so the new simulated_capital is picked up on
    # the next scan instead of waiting for the TTL to elapse.
    auto_trade_engine._cached_settings = None
    auto_trade_engine._settings_cache_time = 0.0


# --- Endpoints --------------------------------------------------------------


@router.post("")
async def create_session(
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Start a new trading session, closing any currently-ACTIVE one."""
    name = str(body.get("session_name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="session_name is required")

    raw_capital = body.get("starting_capital", 100000.0)
    try:
        starting_capital = float(raw_capital)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="starting_capital must be numeric")
    if starting_capital <= 0:
        raise HTTPException(status_code=400, detail="starting_capital must be positive")

    notes = body.get("notes")

    # Close any currently-active session first.
    active = await _get_active_session_row(db)
    closed_payload: Optional[dict] = None
    if active:
        closed_payload = await _close_session(
            db, active, reason="Superseded by new session",
        )

    # Insert the new session.
    row = (await db.execute(text(
        "INSERT INTO trading_sessions "
        "    (session_name, starting_capital, status, created_at, notes) "
        "VALUES (:name, :capital, 'ACTIVE', :ts, :notes) "
        "RETURNING id"
    ), {
        "name": name,
        "capital": starting_capital,
        "ts": datetime.utcnow(),
        "notes": notes,
    })).scalar()
    new_session_id = int(row)

    # Reset simulated_capital so the engine starts from the new starting_capital.
    await db.execute(text(
        "UPDATE trading_settings SET simulated_capital = :cap WHERE id = 1"
    ), {"cap": starting_capital})
    await db.commit()

    _reset_engine_daily_state()

    try:
        await audit.log_event(
            trade_id=0,
            trade_type="SESSION",
            event_type=EVENT_SESSION_STARTED,
            new_value={
                "id": new_session_id,
                "session_name": name,
                "starting_capital": starting_capital,
            },
            reason="New trading session created",
            extra_metadata={"session_id": new_session_id},
        )
        # Tag this audit row with the session_id so the audit endpoint finds it.
        await db.execute(text(
            "UPDATE trade_audit_log SET session_id = :sid "
            " WHERE trade_type = 'SESSION' AND event_type = :evt "
            "   AND extra_metadata::text LIKE :marker"
        ), {
            "sid": new_session_id,
            "evt": EVENT_SESSION_STARTED,
            "marker": f'%"session_id": {new_session_id}%',
        })
        await db.commit()
    except Exception as e:
        logger.debug(f"Audit log for SESSION_STARTED failed: {e}")

    return {
        "id": new_session_id,
        "session_name": name,
        "starting_capital": starting_capital,
        "status": "ACTIVE",
        "closed_previous_session": closed_payload,
    }


@router.get("")
async def list_sessions(
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Return every session, newest first, with live P&L rolled up."""
    rows = (await db.execute(text(
        "SELECT id, session_name, starting_capital, status, created_at, "
        "       closed_at, closing_capital, total_pnl, total_trades, "
        "       win_count, loss_count, notes "
        "  FROM trading_sessions "
        " ORDER BY created_at DESC"
    ))).mappings().all()

    out: List[Dict[str, Any]] = []
    for row in rows:
        r = _row_to_dict(row)
        summary = await _session_summary(db, int(r["id"]))
        r["summary"] = summary
        # For an active session the DB columns are NULL; fall back to the
        # rolled-up summary so the UI always has numbers to display.
        if r["status"] == "ACTIVE":
            r["total_pnl"] = summary["total_pnl"]
            r["total_trades"] = summary["total_trades"]
            r["win_count"] = summary["win_count"]
            r["loss_count"] = summary["loss_count"]
        out.append(r)
    return out


@router.get("/active")
async def active_session(db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Return the currently-ACTIVE session or ``{"active": false}``."""
    row = await _get_active_session_row(db)
    if not row:
        return {"active": False}
    sid = int(row["id"])
    summary = await _session_summary(db, sid)
    out = _row_to_dict(row)
    out["active"] = True
    out["summary"] = summary
    out["running_pnl"] = summary["total_pnl"]
    return out


@router.get("/{session_id}")
async def session_detail(
    session_id: int,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Return a session row plus every paper trade under that session."""
    row = (await db.execute(text(
        "SELECT id, session_name, starting_capital, status, created_at, "
        "       closed_at, closing_capital, total_pnl, total_trades, "
        "       win_count, loss_count, notes "
        "  FROM trading_sessions WHERE id = :id"
    ), {"id": session_id})).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    trade_rows = (await db.execute(text(
        "SELECT id, symbol, side, entry_price, entry_time, exit_price, exit_time, "
        "       quantity, status, result, pnl_amount, pnl_percent, "
        "       COALESCE(net_pnl, pnl_amount) AS net_pnl, "
        "       exit_reason "
        "  FROM paper_trades "
        " WHERE session_id = :sid "
        " ORDER BY entry_time DESC"
    ), {"sid": session_id})).fetchall()

    trades = [
        {
            "id": t[0],
            "symbol": t[1],
            "side": t[2],
            "entry_price": float(t[3]) if t[3] is not None else None,
            "entry_time": str(t[4]) if t[4] else None,
            "exit_price": float(t[5]) if t[5] is not None else None,
            "exit_time": str(t[6]) if t[6] else None,
            "quantity": t[7],
            "status": t[8],
            "result": t[9],
            "pnl_amount": float(t[10]) if t[10] is not None else None,
            "pnl_percent": float(t[11]) if t[11] is not None else None,
            "net_pnl": float(t[12]) if t[12] is not None else None,
            "exit_reason": t[13],
        }
        for t in trade_rows
    ]

    summary = await _session_summary(db, session_id)
    out = _row_to_dict(row)
    out["summary"] = summary
    out["trades"] = trades
    return out


@router.post("/{session_id}/close")
async def close_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Manually close a session (squares off every open paper trade)."""
    row = (await db.execute(text(
        "SELECT id, session_name, starting_capital, status "
        "  FROM trading_sessions WHERE id = :id"
    ), {"id": session_id})).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row["status"] != "ACTIVE":
        raise HTTPException(status_code=400, detail="Session is not active")

    payload = await _close_session(db, dict(row), reason="Closed by user")
    _reset_engine_daily_state()
    return payload


@router.get("/{session_id}/analytics")
async def session_analytics(
    session_id: int,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Return P&L / win-rate analytics for a specific session."""
    row = (await db.execute(text(
        "SELECT id, session_name, starting_capital, status "
        "  FROM trading_sessions WHERE id = :id"
    ), {"id": session_id})).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    stats = (await db.execute(text(
        "SELECT COUNT(*)                                          AS total_trades, "
        "       COUNT(*) FILTER (WHERE status = 'OPEN')           AS open_trades, "
        "       COUNT(*) FILTER (WHERE status = 'CLOSED')         AS closed_trades, "
        "       COUNT(*) FILTER (WHERE result = 'WIN')            AS wins, "
        "       COUNT(*) FILTER (WHERE result = 'LOSS')           AS losses, "
        "       COUNT(*) FILTER (WHERE result = 'BREAKEVEN')      AS breakeven, "
        "       AVG(pnl_percent) FILTER (WHERE result = 'WIN')    AS avg_win, "
        "       AVG(pnl_percent) FILTER (WHERE result = 'LOSS')   AS avg_loss, "
        "       AVG(pnl_percent)                                  AS avg_pnl, "
        "       COALESCE(SUM(pnl_amount), 0)                      AS total_pnl, "
        "       COALESCE(SUM(COALESCE(net_pnl, pnl_amount)), 0)   AS total_net_pnl, "
        "       AVG(duration_minutes)                             AS avg_duration "
        "  FROM paper_trades WHERE session_id = :sid"
    ), {"sid": session_id})).mappings().first() or {}

    total = int(stats.get("total_trades") or 0)
    closed = int(stats.get("closed_trades") or 0)
    wins = int(stats.get("wins") or 0)
    win_rate = (wins / closed * 100) if closed else 0.0

    capital = float(row["starting_capital"] or 0)
    total_pnl = float(stats.get("total_pnl") or 0)
    total_net_pnl = float(stats.get("total_net_pnl") or 0)
    roi_pct = (total_pnl / capital * 100) if capital else 0.0
    net_roi_pct = (total_net_pnl / capital * 100) if capital else 0.0

    return {
        "session_id": session_id,
        "session_name": row["session_name"],
        "starting_capital": capital,
        "status": row["status"],
        "total_trades": total,
        "open_trades": int(stats.get("open_trades") or 0),
        "closed_trades": closed,
        "wins": wins,
        "losses": int(stats.get("losses") or 0),
        "breakeven": int(stats.get("breakeven") or 0),
        "win_rate": round(win_rate, 1),
        "avg_win_pct": round(float(stats.get("avg_win") or 0), 2),
        "avg_loss_pct": round(float(stats.get("avg_loss") or 0), 2),
        "avg_pnl_pct": round(float(stats.get("avg_pnl") or 0), 2),
        "total_pnl": round(total_pnl, 2),
        "total_net_pnl": round(total_net_pnl, 2),
        "avg_duration_minutes": round(float(stats.get("avg_duration") or 0), 0),
        "roi_pct": round(roi_pct, 2),
        "net_roi_pct": round(net_roi_pct, 2),
    }


@router.get("/{session_id}/audit")
async def session_audit(
    session_id: int,
    limit: int = 500,
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Return the audit trail for a session (oldest first).

    Includes session-level events (SESSION_STARTED, SESSION_CLOSED), every
    trade-level event for paper trades in the session, and every engine
    log emitted while the session was active.
    """
    rows = (await db.execute(text(
        "SELECT id, trade_id, trade_type, event_type, symbol, timestamp, "
        "       old_value, new_value, reason, trigger_data, extra_metadata, "
        "       session_id "
        "  FROM trade_audit_log "
        " WHERE session_id = :sid "
        "    OR (trade_type = 'PAPER' "
        "        AND trade_id IN (SELECT id FROM paper_trades WHERE session_id = :sid)) "
        " ORDER BY timestamp ASC, id ASC "
        " LIMIT :limit"
    ), {"sid": session_id, "limit": limit})).fetchall()

    return [
        {
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
            "session_id": r[11],
        }
        for r in rows
    ]
