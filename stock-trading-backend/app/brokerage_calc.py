"""Brokerage and charges calculator for Fyers equity trades.

Supports both **INTRADAY (MIS)** and **DELIVERY (CNC)** rate cards.

Reference:
    - Pricing: https://fyers.in/pricing/
    - Calculator: https://fyers.in/calculator/brokerage/ (Equity → Intraday / Delivery, NSE)

All percentages are expressed as fractions (e.g. 0.03% = 0.0003) and applied
per-side unless otherwise noted. The functions handle a round-trip (entry +
exit) and accept separate buy/sell values so the math stays correct for
SHORT trades too (where the sell leg happens first).
"""

from typing import Optional


# --- Fyers equity rate cards (NSE) ------------------------------------------

# Brokerage — same flat-₹20-cap-per-side for both intraday and delivery.
FYERS_FLAT_BROKERAGE = 20.0     # Rs per order cap
FYERS_BROKERAGE_PCT = 0.0003    # 0.03% of turnover per order (lower of the two)
GST_PCT = 0.18                  # on brokerage + exchange + SEBI + IPFT

# Exchange transaction + regulatory charges — same for intraday and delivery.
EXCHANGE_TXN_PCT = 0.0000297    # 0.00297% on turnover (both sides) — NSE, Oct-2024
SEBI_PCT = 1e-6                 # Rs 10 / crore on turnover (both sides)
IPFT_PCT = 5e-7                 # Rs 10 / 2crore = NSE IPFT, on turnover (both sides)

# Intraday-specific (MIS)
INTRADAY_STT_SELL_PCT = 0.00025     # 0.025% on sell leg only
INTRADAY_STAMP_DUTY_BUY_PCT = 0.00003  # 0.003% on buy value only

# Delivery-specific (CNC)
DELIVERY_STT_PCT = 0.001            # 0.1% on BOTH buy and sell sides
DELIVERY_STAMP_DUTY_BUY_PCT = 0.00015  # 0.015% on buy value only

# Fyers intraday leverage for eligible equities (upper bound — varies per stock,
# this is the notional max used for margin sizing).
INTRADAY_MAX_LEVERAGE = 5.0


def _order_brokerage(order_value: float) -> float:
    """Fyers per-order brokerage: min(flat ₹20, 0.03% × order_value)."""
    if order_value <= 0:
        return 0.0
    return min(FYERS_FLAT_BROKERAGE, order_value * FYERS_BROKERAGE_PCT)


def _calc_common(buy_value: float, sell_value: float) -> tuple:
    """Compute the charges common to both intraday and delivery.

    Returns ``(brokerage, exchange, sebi, ipft)`` — all in rupees.
    """
    brokerage = _order_brokerage(buy_value) + _order_brokerage(sell_value)
    turnover = buy_value + sell_value
    exchange = turnover * EXCHANGE_TXN_PCT
    sebi = turnover * SEBI_PCT
    ipft = turnover * IPFT_PCT
    return brokerage, exchange, sebi, ipft


def calc_brokerage_intraday(
    buy_value: float,
    sell_value: Optional[float] = None,
    qty: int = 1,
) -> dict:
    """Round-trip charges breakdown for an equity **intraday** trade on Fyers."""
    if sell_value is None:
        sell_value = buy_value
    buy_value = max(float(buy_value), 0.0)
    sell_value = max(float(sell_value), 0.0)

    brokerage, exchange, sebi, ipft = _calc_common(buy_value, sell_value)
    stt = sell_value * INTRADAY_STT_SELL_PCT
    stamp = buy_value * INTRADAY_STAMP_DUTY_BUY_PCT
    gst = (brokerage + exchange + sebi + ipft) * GST_PCT

    total = brokerage + stt + exchange + sebi + ipft + stamp + gst

    return {
        "product_type": "INTRADAY",
        "brokerage": round(brokerage, 2),
        "stt": round(stt, 2),
        "exchange_charges": round(exchange, 2),
        "sebi_charges": round(sebi, 4),
        "ipft": round(ipft, 4),
        "stamp_duty": round(stamp, 2),
        "gst": round(gst, 2),
        "total_charges": round(total, 2),
    }


def calc_brokerage_delivery(
    buy_value: float,
    sell_value: Optional[float] = None,
    qty: int = 1,
) -> dict:
    """Round-trip charges breakdown for an equity **delivery** trade on Fyers."""
    if sell_value is None:
        sell_value = buy_value
    buy_value = max(float(buy_value), 0.0)
    sell_value = max(float(sell_value), 0.0)

    brokerage, exchange, sebi, ipft = _calc_common(buy_value, sell_value)
    # STT charged at 0.1% on BOTH legs for delivery.
    stt = (buy_value + sell_value) * DELIVERY_STT_PCT
    stamp = buy_value * DELIVERY_STAMP_DUTY_BUY_PCT
    gst = (brokerage + exchange + sebi + ipft) * GST_PCT

    total = brokerage + stt + exchange + sebi + ipft + stamp + gst

    return {
        "product_type": "DELIVERY",
        "brokerage": round(brokerage, 2),
        "stt": round(stt, 2),
        "exchange_charges": round(exchange, 2),
        "sebi_charges": round(sebi, 4),
        "ipft": round(ipft, 4),
        "stamp_duty": round(stamp, 2),
        "gst": round(gst, 2),
        "total_charges": round(total, 2),
    }


def calc_brokerage(
    buy_value: float,
    sell_value: Optional[float] = None,
    qty: int = 1,
    product_type: str = "INTRADAY",
) -> dict:
    """Compute round-trip charges for an equity trade on Fyers.

    ``product_type`` selects between **INTRADAY** (MIS) and **DELIVERY** (CNC)
    rate cards. Defaults to INTRADAY for backwards compatibility with legacy
    callers that pre-date dual-mode support.
    """
    pt = (product_type or "INTRADAY").upper()
    if pt == "DELIVERY" or pt == "CNC":
        return calc_brokerage_delivery(buy_value, sell_value, qty)
    return calc_brokerage_intraday(buy_value, sell_value, qty)


def estimate_round_trip_cost(
    entry_price: float,
    qty: int,
    exit_price: Optional[float] = None,
    product_type: str = "INTRADAY",
) -> float:
    """Estimate total round-trip charges in rupees for a given product type.

    Callers that don't know the exit side yet can omit ``exit_price`` — we
    fall back to ``entry_price`` (i.e. zero P&L assumption, which is a good
    cost estimate when the move is small relative to price).
    """
    buy_value = entry_price * qty
    sell_value = (exit_price if exit_price is not None else entry_price) * qty
    return calc_brokerage(buy_value, sell_value, qty, product_type)["total_charges"]


def _buy_sell_values(entry_price: float, exit_price: float, qty: int):
    """Return (buy_value, sell_value) for a round-trip, handling SHORT.

    For a LONG, we buy at ``entry_price`` and sell at ``exit_price``. For a
    SHORT, the sell leg is at the higher price and the buy (cover) is at
    the lower one. We detect SHORT by ``exit_price < entry_price``.
    """
    if exit_price >= entry_price:
        return entry_price * qty, exit_price * qty
    return exit_price * qty, entry_price * qty


def is_trade_profitable_after_brokerage(
    entry_price: float,
    target_price: float,
    qty: int,
    min_profit_ratio: float = 2.0,
    min_net_profit: float = 0.0,
    product_type: str = "INTRADAY",
) -> dict:
    """Check if the expected trade clears both the ratio gate and net-profit floor.

    Pass condition: ``gross_profit >= total_charges * min_profit_ratio``
    **AND** ``net_profit >= min_net_profit``. The ratio gate is a guard
    against low-edge setups; the net-profit floor is the user-configured
    "don't bother with ₹5 trades" threshold.
    """
    qty = int(qty)
    buy_value, sell_value = _buy_sell_values(entry_price, target_price, qty)
    gross_profit = abs(target_price - entry_price) * qty
    charges = calc_brokerage(buy_value, sell_value, qty, product_type)
    total_cost = charges["total_charges"]
    net_profit = gross_profit - total_cost

    ratio_ok = gross_profit >= total_cost * min_profit_ratio
    net_ok = net_profit >= min_net_profit

    return {
        "profitable": bool(ratio_ok and net_ok),
        "product_type": charges["product_type"],
        "gross_profit": round(gross_profit, 2),
        "total_charges": round(total_cost, 2),
        "net_profit": round(net_profit, 2),
        "profit_to_cost_ratio": (
            round(gross_profit / total_cost, 2) if total_cost > 0 else 999.0
        ),
        "min_required_ratio": min_profit_ratio,
        "min_net_profit": min_net_profit,
        "ratio_ok": bool(ratio_ok),
        "net_ok": bool(net_ok),
        "charges_breakdown": charges,
    }


def min_qty_for_net_profit(
    entry_price: float,
    target_price: float,
    min_net_profit: float = 100.0,
    max_qty: int = 100000,
    min_profit_ratio: float = 2.0,
    product_type: str = "INTRADAY",
) -> int:
    """Smallest qty where the trade clears both profit gates.

    Returns 0 if no qty up to ``max_qty`` satisfies the constraints (usually
    because the per-share edge is thinner than per-share charges).

    Net profit is monotonically non-decreasing in qty (the ₹20 brokerage
    cap saturates and all other fees scale linearly), so binary search is
    safe once we verify the top of the range clears the gate.
    """
    if entry_price <= 0 or target_price <= 0 or max_qty < 1:
        return 0
    if abs(target_price - entry_price) <= 0:
        return 0

    top = is_trade_profitable_after_brokerage(
        entry_price, target_price, max_qty,
        min_profit_ratio=min_profit_ratio, min_net_profit=min_net_profit,
        product_type=product_type,
    )
    if not top["profitable"]:
        return 0

    lo, hi = 1, max_qty
    while lo < hi:
        mid = (lo + hi) // 2
        r = is_trade_profitable_after_brokerage(
            entry_price, target_price, mid,
            min_profit_ratio=min_profit_ratio, min_net_profit=min_net_profit,
            product_type=product_type,
        )
        if r["profitable"]:
            hi = mid
        else:
            lo = mid + 1
    return lo


def compare_intraday_vs_delivery(
    buy_price: float,
    sell_price: float,
    qty: int,
) -> dict:
    """Return both intraday & delivery breakdowns side-by-side with a recommendation.

    Recommendation picks whichever mode has the higher **net profit**, with
    an absolute tie-breaker of: prefer DELIVERY when net profits are equal
    (delivery has no time-of-day constraint). If neither mode clears a
    positive net profit, we still return the comparison; callers should
    treat the ``recommendation`` as advisory.
    """
    qty = max(int(qty), 0)
    gross_profit = (sell_price - buy_price) * qty

    buy_value, sell_value = _buy_sell_values(buy_price, sell_price, qty)
    intraday_charges = calc_brokerage_intraday(buy_value, sell_value, qty)
    delivery_charges = calc_brokerage_delivery(buy_value, sell_value, qty)

    intraday_net = gross_profit - intraday_charges["total_charges"]
    delivery_net = gross_profit - delivery_charges["total_charges"]

    if intraday_net > delivery_net:
        recommendation = "INTRADAY"
        reason = (
            f"Intraday keeps ₹{intraday_net:.2f} vs delivery's ₹{delivery_net:.2f} "
            "— lower STT on a single-day round trip outweighs the extra stamp duty."
        )
    elif delivery_net > intraday_net:
        recommendation = "DELIVERY"
        reason = (
            f"Delivery keeps ₹{delivery_net:.2f} vs intraday's ₹{intraday_net:.2f} "
            "— when the charge diff is close, delivery avoids the 3:15 PM square-off."
        )
    else:
        recommendation = "DELIVERY"
        reason = "Net profit identical — preferring delivery (no time-of-day constraint)."

    return {
        "buy_price": round(buy_price, 2),
        "sell_price": round(sell_price, 2),
        "qty": qty,
        "gross_profit": round(gross_profit, 2),
        "intraday": {
            "charges": intraday_charges,
            "total_charges": intraday_charges["total_charges"],
            "net_profit": round(intraday_net, 2),
            "charges_pct_of_gross": (
                round(intraday_charges["total_charges"] / gross_profit * 100, 2)
                if gross_profit > 0 else None
            ),
        },
        "delivery": {
            "charges": delivery_charges,
            "total_charges": delivery_charges["total_charges"],
            "net_profit": round(delivery_net, 2),
            "charges_pct_of_gross": (
                round(delivery_charges["total_charges"] / gross_profit * 100, 2)
                if gross_profit > 0 else None
            ),
        },
        "recommendation": recommendation,
        "recommendation_reason": reason,
    }


def calc_max_intraday_quantity(
    available_margin: float,
    price: float,
    leverage: float = INTRADAY_MAX_LEVERAGE,
) -> int:
    """Max shares buyable on intraday given available margin and leverage.

    Fyers MIS on eligible equities offers up to ~5x leverage (varies per
    stock). Callers that want the conservative, no-leverage number should
    pass ``leverage=1``.
    """
    if available_margin <= 0 or price <= 0 or leverage <= 0:
        return 0
    return int((available_margin * leverage) // price)


def calc_max_delivery_quantity(
    available_margin: float,
    price: float,
) -> int:
    """Max shares buyable on delivery (CNC) — requires full margin, no leverage."""
    if available_margin <= 0 or price <= 0:
        return 0
    return int(available_margin // price)
