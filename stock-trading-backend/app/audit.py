"""Append-only audit log for trades.

Every material change to a trade — placement, closure, SL / target moves,
timing decisions, product-type routing — goes into ``trade_audit_log``.
The Trade Journal UI consumes this via ``routers.audit`` to show a
per-trade event timeline.

All writes go through ``log_event()`` which is safe to fire-and-forget: a
failing insert never raises to the caller, it just logs a warning and
moves on. The trading loop should never be blocked by audit bookkeeping.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import text

from app.database import async_session_factory


logger = logging.getLogger(__name__)


# Canonical event_type values. Strings are kept short so they sort cleanly
# in the DB but remain human-readable in the UI.
EVENT_TRADE_PLACED = "TRADE_PLACED"
EVENT_TRADE_CLOSED = "TRADE_CLOSED"
EVENT_SL_CHANGED = "SL_CHANGED"
EVENT_TARGET_CHANGED = "TARGET_CHANGED"
EVENT_TRAILING_PROFIT = "TRAILING_PROFIT"
EVENT_TREND_REVERSAL = "TREND_REVERSAL"
EVENT_REANALYSIS = "REANALYSIS"
EVENT_INTRADAY_SQUARE_OFF = "INTRADAY_SQUARE_OFF"
EVENT_PRODUCT_TYPE_DECISION = "PRODUCT_TYPE_DECISION"
EVENT_TIMING_REJECTED = "TIMING_REJECTED"


def _dump(val: Any) -> Optional[str]:
    if val is None:
        return None
    try:
        return json.dumps(val, default=str)
    except Exception:
        return json.dumps({"repr": repr(val)})


async def log_event(
    trade_id: int,
    trade_type: str,
    event_type: str,
    *,
    symbol: Optional[str] = None,
    old_value: Optional[dict] = None,
    new_value: Optional[dict] = None,
    reason: Optional[str] = None,
    trigger_data: Optional[dict] = None,
    extra_metadata: Optional[dict] = None,
) -> None:
    """Insert a single audit row. Never raises."""
    try:
        async with async_session_factory() as db:
            await db.execute(
                text(
                    "INSERT INTO trade_audit_log "
                    "(trade_id, trade_type, event_type, symbol, timestamp, "
                    " old_value, new_value, reason, trigger_data, extra_metadata) "
                    "VALUES (:trade_id, :trade_type, :event_type, :symbol, :ts, "
                    " CAST(:old_value AS JSON), CAST(:new_value AS JSON), :reason, "
                    " CAST(:trigger_data AS JSON), CAST(:extra_metadata AS JSON))"
                ),
                {
                    "trade_id": int(trade_id),
                    "trade_type": (trade_type or "PAPER").upper(),
                    "event_type": event_type,
                    "symbol": symbol,
                    "ts": datetime.utcnow(),
                    "old_value": _dump(old_value),
                    "new_value": _dump(new_value),
                    "reason": reason,
                    "trigger_data": _dump(trigger_data),
                    "extra_metadata": _dump(extra_metadata),
                },
            )
            await db.commit()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(
            "Audit log insert failed (trade=%s/%s, event=%s): %s",
            trade_type, trade_id, event_type, e,
        )


async def log_event_sync(
    db,
    trade_id: int,
    trade_type: str,
    event_type: str,
    *,
    symbol: Optional[str] = None,
    old_value: Optional[dict] = None,
    new_value: Optional[dict] = None,
    reason: Optional[str] = None,
    trigger_data: Optional[dict] = None,
    extra_metadata: Optional[dict] = None,
) -> None:
    """Insert an audit row using an existing AsyncSession (no commit).

    Caller is responsible for committing. Useful when we want the audit
    write to be atomic with another DB mutation (e.g. trade insert).
    """
    try:
        await db.execute(
            text(
                "INSERT INTO trade_audit_log "
                "(trade_id, trade_type, event_type, symbol, timestamp, "
                " old_value, new_value, reason, trigger_data, extra_metadata) "
                "VALUES (:trade_id, :trade_type, :event_type, :symbol, :ts, "
                " CAST(:old_value AS JSON), CAST(:new_value AS JSON), :reason, "
                " CAST(:trigger_data AS JSON), CAST(:extra_metadata AS JSON))"
            ),
            {
                "trade_id": int(trade_id),
                "trade_type": (trade_type or "PAPER").upper(),
                "event_type": event_type,
                "symbol": symbol,
                "ts": datetime.utcnow(),
                "old_value": _dump(old_value),
                "new_value": _dump(new_value),
                "reason": reason,
                "trigger_data": _dump(trigger_data),
                "extra_metadata": _dump(extra_metadata),
            },
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(
            "Audit log (sync) insert failed (trade=%s/%s, event=%s): %s",
            trade_type, trade_id, event_type, e,
        )
