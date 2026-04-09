"""Day P&L Limits Engine - enforces per-day max loss and profit targets."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


class DayLimitsEngine:
    """Enforces per-day max loss and profit targets for both PAPER and LIVE modes."""

    async def get_day_status(self, db: AsyncSession, mode: str) -> dict:
        """Returns current day P&L status for given mode."""
        table = "paper_trades" if mode == "PAPER" else "live_trades"
        pnl_col = "pnl_amount" if mode == "PAPER" else "net_pnl"
        today = datetime.now(IST).date()

        # Get settings
        settings_row = await db.execute(text("SELECT * FROM trading_settings WHERE id=1"))
        settings = settings_row.mappings().first()
        if not settings:
            return self._default_status(mode)

        # Realized P&L from closed trades today
        result = await db.execute(text(f"""
            SELECT COALESCE(SUM({pnl_col}), 0) as total_pnl
            FROM {table}
            WHERE DATE(exit_time) = :today
              AND status != 'OPEN'
        """), {"today": today})
        row = result.mappings().first()
        realized = float(row["total_pnl"]) if row else 0.0

        mode_lower = mode.lower()
        day_loss_limit = float(settings[f"day_max_loss_{mode_lower}"])
        day_profit_target = float(settings[f"day_profit_target_{mode_lower}"])

        is_loss_limit_hit = realized <= day_loss_limit
        is_profit_target_hit = realized >= day_profit_target

        loss_consumed_pct = (realized / day_loss_limit * 100) if day_loss_limit != 0 else 0
        profit_achieved_pct = (realized / day_profit_target * 100) if day_profit_target > 0 else 0

        return {
            "mode": mode,
            "realized_pnl": round(realized, 2),
            "unrealized_pnl": 0.0,
            "net_pnl": round(realized, 2),
            "day_loss_limit": day_loss_limit,
            "day_profit_target": day_profit_target,
            "loss_consumed_pct": round(max(0, min(100, abs(loss_consumed_pct))), 1),
            "profit_achieved_pct": round(max(0, min(100, profit_achieved_pct)), 1),
            "is_loss_limit_hit": is_loss_limit_hit,
            "is_profit_target_hit": is_profit_target_hit,
            "trading_allowed": not is_loss_limit_hit and not is_profit_target_hit,
            "stop_reason": (
                "MAX LOSS HIT - Trading stopped for today"
                if is_loss_limit_hit
                else (
                    "PROFIT TARGET HIT - Great day! Trading stopped"
                    if is_profit_target_hit
                    else None
                )
            ),
        }

    async def check_before_trade(self, db: AsyncSession, mode: str) -> tuple:
        """Check if trading is allowed. Returns (can_trade, reason)."""
        status = await self.get_day_status(db, mode)

        if status["is_loss_limit_hit"]:
            return False, f"Day loss limit hit: {abs(status['day_loss_limit'])}"
        if status["is_profit_target_hit"]:
            return False, f"Day profit target achieved: {status['day_profit_target']}"
        return True, "OK"

    def _default_status(self, mode: str) -> dict:
        return {
            "mode": mode,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "net_pnl": 0.0,
            "day_loss_limit": -1000.0 if mode == "PAPER" else -2000.0,
            "day_profit_target": 3000.0 if mode == "PAPER" else 5000.0,
            "loss_consumed_pct": 0.0,
            "profit_achieved_pct": 0.0,
            "is_loss_limit_hit": False,
            "is_profit_target_hit": False,
            "trading_allowed": True,
            "stop_reason": None,
        }


day_limits_engine = DayLimitsEngine()
