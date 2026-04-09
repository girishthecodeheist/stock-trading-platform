"""Brokerage and charges calculator for live trades (Fyers)."""


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
