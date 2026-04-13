"""Brokerage and charges calculator for live and paper trades."""


def calc_brokerage(trade_value: float, qty: int = 1) -> dict:
    """Calculate all trading charges for a live trade.
    
    Fyers flat fee: Rs.20 per order or 0.03% whichever is lower.
    """
    broker_charge = min(20.0, trade_value * 0.0003)
    brokerage = broker_charge * 2  # entry + exit
    stt = trade_value * 0.00025  # 0.025% on sell side
    exchange_charges = trade_value * 0.0000345  # NSE charges
    gst_val = (brokerage + exchange_charges) * 0.18
    sebi_charges = trade_value * 0.000001
    stamp_duty = trade_value * 0.00015 / 2

    total = brokerage + stt + exchange_charges + gst_val + sebi_charges + stamp_duty

    return {
        "brokerage": round(brokerage, 2),
        "stt": round(stt, 2),
        "exchange_charges": round(exchange_charges, 2),
        "gst": round(gst_val, 2),
        "sebi_charges": round(sebi_charges, 4),
        "stamp_duty": round(stamp_duty, 2),
        "total_charges": round(total, 2),
    }


def estimate_round_trip_cost(entry_price: float, qty: int) -> float:
    """Estimate total round-trip brokerage cost for a trade (entry + exit).

    Used by the auto-trade engine to filter out trades where
    brokerage would eat the expected profit.
    Returns the total charges in rupees.
    """
    trade_value = entry_price * qty
    charges = calc_brokerage(trade_value, qty)
    return charges["total_charges"]


def is_trade_profitable_after_brokerage(
    entry_price: float, target_price: float, qty: int,
    min_profit_ratio: float = 2.0,
) -> dict:
    """Check if expected profit exceeds brokerage by at least min_profit_ratio.

    Returns dict with pass/fail and breakdown.
    min_profit_ratio=2.0 means expected profit must be >= 2x brokerage.
    """
    trade_value = entry_price * qty
    gross_profit = abs(target_price - entry_price) * qty
    charges = calc_brokerage(trade_value, qty)
    total_cost = charges["total_charges"]
    net_profit = gross_profit - total_cost

    return {
        "profitable": net_profit >= total_cost * (min_profit_ratio - 1),
        "gross_profit": round(gross_profit, 2),
        "total_charges": round(total_cost, 2),
        "net_profit": round(net_profit, 2),
        "profit_to_cost_ratio": round(gross_profit / total_cost, 2) if total_cost > 0 else 999,
        "min_required_ratio": min_profit_ratio,
    }
