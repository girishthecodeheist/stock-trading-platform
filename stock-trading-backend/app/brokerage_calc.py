"""Brokerage and charges calculator for Fyers equity **intraday** trades.

Reference:
    - Pricing: https://fyers.in/pricing/
    - Calculator: https://fyers.in/calculator/brokerage/ (Equity → Intraday, NSE)

All percentages are expressed as fractions (e.g. 0.03% = 0.0003) and applied
per-side unless otherwise noted. The functions handle a round-trip (entry +
exit) and accept separate buy/sell values so the math stays correct for
SHORT trades too (where the sell leg happens first).
"""

from typing import Optional


# --- Fyers equity intraday rate card (NSE) ----------------------------------

FYERS_FLAT_BROKERAGE = 20.0     # Rs per order cap
FYERS_BROKERAGE_PCT = 0.0003    # 0.03% of turnover per order (lower of the two)
STT_SELL_PCT = 0.00025          # 0.025% on sell leg only
EXCHANGE_TXN_PCT = 0.0000297    # 0.00297% on turnover (both sides) — NSE, Oct-2024
SEBI_PCT = 1e-6                 # Rs 10 / crore on turnover (both sides)
IPFT_PCT = 5e-7                 # Rs 10 / 2crore = NSE IPFT, on turnover (both sides)
STAMP_DUTY_BUY_PCT = 0.00003    # 0.003% on buy value only — intraday rate
GST_PCT = 0.18                  # on brokerage + exchange + SEBI + IPFT


def _order_brokerage(order_value: float) -> float:
    """Fyers per-order brokerage: min(flat ₹20, 0.03% × order_value)."""
    if order_value <= 0:
        return 0.0
    return min(FYERS_FLAT_BROKERAGE, order_value * FYERS_BROKERAGE_PCT)


def calc_brokerage(
    buy_value: float,
    sell_value: Optional[float] = None,
    qty: int = 1,
) -> dict:
    """Compute round-trip charges for an equity intraday trade on Fyers.

    ``sell_value`` defaults to ``buy_value`` for legacy callers that only
    know one leg (e.g. at entry time). ``qty`` is kept for signature
    compatibility — rates are all value-based so it's informational only.
    """
    if sell_value is None:
        sell_value = buy_value
    buy_value = max(float(buy_value), 0.0)
    sell_value = max(float(sell_value), 0.0)
    turnover = buy_value + sell_value

    brokerage_buy = _order_brokerage(buy_value)
    brokerage_sell = _order_brokerage(sell_value)
    brokerage = brokerage_buy + brokerage_sell

    stt = sell_value * STT_SELL_PCT
    exchange = turnover * EXCHANGE_TXN_PCT
    sebi = turnover * SEBI_PCT
    ipft = turnover * IPFT_PCT
    stamp = buy_value * STAMP_DUTY_BUY_PCT
    gst = (brokerage + exchange + sebi + ipft) * GST_PCT

    total = brokerage + stt + exchange + sebi + ipft + stamp + gst

    return {
        "brokerage": round(brokerage, 2),
        "stt": round(stt, 2),
        "exchange_charges": round(exchange, 2),
        "sebi_charges": round(sebi, 4),
        "ipft": round(ipft, 4),
        "stamp_duty": round(stamp, 2),
        "gst": round(gst, 2),
        "total_charges": round(total, 2),
    }


def estimate_round_trip_cost(
    entry_price: float,
    qty: int,
    exit_price: Optional[float] = None,
) -> float:
    """Estimate total round-trip charges in rupees.

    Callers that don't know the exit side yet can omit ``exit_price`` — we
    fall back to ``entry_price`` (i.e. zero P&L assumption, which is a good
    cost estimate when the move is small relative to price).
    """
    buy_value = entry_price * qty
    sell_value = (exit_price if exit_price is not None else entry_price) * qty
    return calc_brokerage(buy_value, sell_value, qty)["total_charges"]


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
    charges = calc_brokerage(buy_value, sell_value, qty)
    total_cost = charges["total_charges"]
    net_profit = gross_profit - total_cost

    ratio_ok = gross_profit >= total_cost * min_profit_ratio
    net_ok = net_profit >= min_net_profit

    return {
        "profitable": bool(ratio_ok and net_ok),
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
    )
    if not top["profitable"]:
        return 0

    lo, hi = 1, max_qty
    while lo < hi:
        mid = (lo + hi) // 2
        r = is_trade_profitable_after_brokerage(
            entry_price, target_price, mid,
            min_profit_ratio=min_profit_ratio, min_net_profit=min_net_profit,
        )
        if r["profitable"]:
            hi = mid
        else:
            lo = mid + 1
    return lo
