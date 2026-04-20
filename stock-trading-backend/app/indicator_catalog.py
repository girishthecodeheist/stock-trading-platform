"""Central catalog of every indicator + gate that influences signal/trade flow.

The Indicators Control UI reads from here to build its on/off toggle table and
its numeric-override table for gates. Keeping the catalog in one module means
signal_engine / auto_trade_engine and the API router always agree on what keys
exist and what they mean — no drift between the two sides of the toggle pair.

## Conventions

Every indicator key is a stable machine-string used in the settings table's
``disabled_indicators`` JSON array. Once published, a key should not be
renamed; if an indicator is retired, leave the key in the catalog as
``retired=True`` so old persisted disabled_indicators rows still validate.

``max_contribution`` is the absolute maximum the indicator can push the
combined score (after category weighting, ``|contribution|`` ≤ max_contribution
in most code paths). It's a UI hint — the actual scoring lives in
``signal_engine._analyze_technical`` and ``fundamental_engine._compute_fundamental_score``.

Gate keys (``gate_overrides`` dict on trading_settings) hold a numeric
threshold override. ``None`` / missing = use the engine default from
auto_trade_engine module-level constants or settings columns.
"""

from typing import Any, Dict, List


# --- TECHNICAL INDICATORS -----------------------------------------------------

TECHNICAL_INDICATORS: List[Dict[str, Any]] = [
    {
        "key": "rsi",
        "name": "RSI (14)",
        "category": "technical",
        "max_contribution": 15,
        "description": "Relative Strength Index — oversold (<30) adds bullish score, "
                       "overbought (>70) adds bearish score.",
    },
    {
        "key": "macd",
        "name": "MACD",
        "category": "technical",
        "max_contribution": 15,
        "description": "MACD line vs signal line with histogram confirmation.",
    },
    {
        "key": "moving_averages",
        "name": "Moving Averages (SMA 20/50/200)",
        "category": "technical",
        "max_contribution": 15,
        "description": "Alignment of price vs SMA20, SMA50, SMA200 for trend.",
    },
    {
        "key": "supertrend",
        "name": "Supertrend",
        "category": "technical",
        "max_contribution": 8,
        "description": "Supertrend bullish / bearish direction.",
    },
    {
        "key": "adx",
        "name": "ADX / Directional Index",
        "category": "technical",
        "max_contribution": 5,
        "description": "ADX>25 with DI+ vs DI- for trend strength.",
    },
    {
        "key": "ichimoku",
        "name": "Ichimoku Cloud",
        "category": "technical",
        "max_contribution": 5,
        "description": "Price position vs Ichimoku cloud (above = bullish).",
    },
    {
        "key": "stochastic",
        "name": "Stochastic %K/%D",
        "category": "technical",
        "max_contribution": 5,
        "description": "Stochastic oscillator oversold / overbought.",
    },
    {
        "key": "volume",
        "name": "Volume Analysis",
        "category": "technical",
        "max_contribution": 8,
        "description": "Volume ratio vs 20d avg. High volume confirms, low volume "
                       "discounts the total technical score (multiplicative haircut).",
    },
    {
        "key": "williams_r",
        "name": "Williams %R",
        "category": "technical",
        "max_contribution": 3,
        "description": "Williams %R oversold (<-80) / overbought (>-20).",
    },
    {
        "key": "cci",
        "name": "CCI (20)",
        "category": "technical",
        "max_contribution": 3,
        "description": "Commodity Channel Index oversold (<-100) / overbought (>100).",
    },
    {
        "key": "mfi",
        "name": "Money Flow Index",
        "category": "technical",
        "max_contribution": 3,
        "description": "MFI — volume-weighted RSI; oversold (<20) / overbought (>80).",
    },
    {
        "key": "obv",
        "name": "On-Balance Volume Trend",
        "category": "technical",
        "max_contribution": 3,
        "description": "OBV trend — accumulation vs distribution.",
    },
    {
        "key": "bollinger",
        "name": "Bollinger Bands (%B)",
        "category": "technical",
        "max_contribution": 3,
        "description": "Price at/below lower band = bullish; at/above upper = bearish.",
    },
    {
        "key": "vwap",
        "name": "VWAP",
        "category": "technical",
        "max_contribution": 2,
        "description": "Price vs Volume-Weighted Average Price.",
    },
    {
        "key": "candlestick",
        "name": "Candlestick Patterns",
        "category": "technical",
        "max_contribution": 10,
        "description": "Detected bullish/bearish candlestick patterns "
                       "(hammer, engulfing, doji, etc.) with strength multiplier.",
    },
    {
        "key": "swing_points",
        "name": "Swing Points",
        "category": "technical",
        "max_contribution": 3,
        "description": "Price proximity to recent swing high (resistance) / swing low (support).",
    },
    {
        "key": "trend_strength",
        "name": "Trend Strength (price action)",
        "category": "technical",
        "max_contribution": 3,
        "description": "Classification of strong uptrend / strong downtrend from raw price action.",
    },
    {
        "key": "guppy",
        "name": "Guppy GMMA",
        "category": "technical",
        "max_contribution": 5,
        "description": "Guppy Multiple Moving Average — short EMAs vs long EMAs spread.",
    },
]


# --- FUNDAMENTAL INDICATORS (per-metric) --------------------------------------

FUNDAMENTAL_INDICATORS: List[Dict[str, Any]] = [
    {
        "key": "fund_pe_ratio",
        "name": "P/E Ratio",
        "category": "fundamental",
        "max_contribution": 15,
        "description": "Trailing price/earnings ratio. Lower = cheaper.",
    },
    {
        "key": "fund_pb_ratio",
        "name": "P/B Ratio",
        "category": "fundamental",
        "max_contribution": 10,
        "description": "Price to book. Lower = cheaper.",
    },
    {
        "key": "fund_roe",
        "name": "Return on Equity",
        "category": "fundamental",
        "max_contribution": 12,
        "description": "Return on Equity. Higher = more efficient.",
    },
    {
        "key": "fund_roa",
        "name": "Return on Assets",
        "category": "fundamental",
        "max_contribution": 8,
        "description": "Return on Assets.",
    },
    {
        "key": "fund_debt_to_equity",
        "name": "Debt / Equity",
        "category": "fundamental",
        "max_contribution": 10,
        "description": "Leverage. Lower = less risk.",
    },
    {
        "key": "fund_profit_margin",
        "name": "Profit Margin",
        "category": "fundamental",
        "max_contribution": 10,
        "description": "Net profit margin. Higher = healthier.",
    },
    {
        "key": "fund_revenue_growth",
        "name": "Revenue Growth",
        "category": "fundamental",
        "max_contribution": 10,
        "description": "YoY revenue growth.",
    },
    {
        "key": "fund_earnings_growth",
        "name": "Earnings Growth",
        "category": "fundamental",
        "max_contribution": 10,
        "description": "YoY earnings growth.",
    },
    {
        "key": "fund_current_ratio",
        "name": "Current Ratio",
        "category": "fundamental",
        "max_contribution": 8,
        "description": "Current assets / current liabilities — short-term liquidity.",
    },
    {
        "key": "fund_beta",
        "name": "Beta",
        "category": "fundamental",
        "max_contribution": 7,
        "description": "Beta vs market. 0.8–1.2 = stable; outside = more volatile.",
    },
]


# --- SENTIMENT INDICATORS -----------------------------------------------------
#
# Sentiment today is a single scalar (``sentiment_score``) computed as the
# average TextBlob polarity across yfinance news headlines (with a
# keyword-dict fallback inside the same pipeline). There isn't a clean
# per-metric decomposition until the news engine itself produces multiple
# independent signals (e.g. analyst ratings tone, social-media sentiment,
# earnings-call transcript sentiment). We expose a single monolithic toggle
# for now and will expand this list once news_engine grows.

SENTIMENT_INDICATORS: List[Dict[str, Any]] = [
    {
        "key": "sentiment",
        "name": "News Sentiment (aggregate)",
        "category": "sentiment",
        "max_contribution": 100,
        "description": "Aggregate TextBlob sentiment across recent yfinance news headlines "
                       "(with keyword fallback). Monolithic today — decomposes into per-source "
                       "signals once the news engine has more inputs.",
    },
]


ALL_INDICATORS: List[Dict[str, Any]] = (
    TECHNICAL_INDICATORS + FUNDAMENTAL_INDICATORS + SENTIMENT_INDICATORS
)

_INDICATOR_KEYS = {ind["key"] for ind in ALL_INDICATORS}


def is_valid_indicator_key(key: str) -> bool:
    """Return True if ``key`` is a known indicator catalog entry."""
    return key in _INDICATOR_KEYS


def normalize_disabled_indicators(raw: Any) -> List[str]:
    """Coerce a user-submitted disabled list into a clean sorted list.

    Drops unknown keys silently (catalog is authoritative — typos become
    no-ops rather than crashing the scan loop). Deduplicates.
    """
    if not raw:
        return []
    if isinstance(raw, str):
        # Tolerate a comma-separated string just in case.
        items = [x.strip() for x in raw.split(",") if x.strip()]
    else:
        try:
            items = [str(x) for x in raw]
        except TypeError:
            return []
    return sorted({k for k in items if k in _INDICATOR_KEYS})


# --- GATES --------------------------------------------------------------------
#
# Gates are the filters that decide whether a *computed* signal can become a
# live order. Unlike indicators, gates are numeric thresholds the user can
# override — a stricter or looser value rather than on/off.
#
# The ``settings_field`` column points at the column on ``trading_settings``
# that already persists the threshold where one exists (so we prefer the
# existing plumbing over a second copy inside gate_overrides). For gates
# whose threshold currently lives only as a module constant in
# auto_trade_engine, ``settings_field`` is ``None`` and the override is
# read from the ``gate_overrides`` JSON column instead.

GATES: List[Dict[str, Any]] = [
    {
        "key": "min_confidence",
        "name": "Minimum confidence to place trade",
        "default": 30,
        "min": 0,
        "max": 100,
        "step": 1,
        "settings_field": "min_confidence_for_trade",
        "description": "Signals with confidence below this number are rejected before order "
                       "placement. Lower = more trades; higher = fewer, higher-quality trades.",
    },
    {
        "key": "min_score",
        "name": "Minimum |score| to place trade",
        "default": 25,
        "min": 0,
        "max": 100,
        "step": 1,
        "settings_field": "min_score_for_trade",
        "description": "Signals with absolute score below this are rejected. Runs in parallel "
                       "with min_confidence.",
    },
    {
        "key": "min_net_profit_per_trade",
        "name": "Minimum net profit per trade (₹)",
        "default": 1.0,
        "min": 0.0,
        "max": 10000.0,
        "step": 0.5,
        "settings_field": "min_net_profit_per_trade",
        "description": "The brokerage-aware gate rejects a trade whose expected "
                       "net profit (gross − charges) is below this floor.",
    },
    {
        "key": "min_profit_to_cost_ratio",
        "name": "Minimum profit-to-cost ratio",
        "default": 1.0,
        "min": 0.0,
        "max": 10.0,
        "step": 0.1,
        "settings_field": "min_profit_to_cost_ratio",
        "description": "gross_profit must be >= total_charges × this ratio. "
                       ">=1.0 = any net profit accepted; higher = edge-quality filter.",
    },
    {
        "key": "max_trades_per_day",
        "name": "Max trades per day",
        "default": 50,
        "min": 1,
        "max": 500,
        "step": 1,
        "settings_field": "max_trades_per_day",
        "description": "Hard cap on total trades placed across all symbols in a single day.",
    },
    {
        "key": "max_open_trades",
        "name": "Max concurrent open trades",
        "default": 5,
        "min": 1,
        "max": 50,
        "step": 1,
        "settings_field": "max_open_trades",
        "description": "Cap on simultaneously open positions.",
    },
    # Boolean-flavoured gates below live in ``gate_overrides`` JSON; treat
    # 0/1 as disabled/enabled in the UI.
    {
        "key": "regime_confirm",
        "name": "Require market-regime confirmation",
        "default": 1,
        "min": 0,
        "max": 1,
        "step": 1,
        "settings_field": None,
        "description": "If enabled (1), reject signals whose direction conflicts with the "
                       "Nifty-based market regime (bull/bear/range). Disable to ignore regime.",
    },
    {
        "key": "duplicate_guard",
        "name": "Duplicate symbol cooldown",
        "default": 1,
        "min": 0,
        "max": 1,
        "step": 1,
        "settings_field": None,
        "description": "If enabled (1), reject a second signal on the same symbol within the "
                       "cooldown window. Disable to allow rapid re-entries.",
    },
]

_GATE_KEYS = {g["key"] for g in GATES}


def is_valid_gate_key(key: str) -> bool:
    return key in _GATE_KEYS


def normalize_gate_overrides(raw: Any) -> Dict[str, float]:
    """Sanitise user-submitted gate_overrides dict.

    Drops unknown keys. Clamps values into each gate's ``[min, max]``
    range. Non-numeric values are dropped. The result is safe to persist
    directly to the JSON column.
    """
    if not raw or not isinstance(raw, dict):
        return {}
    bounds = {g["key"]: (g["min"], g["max"]) for g in GATES}
    out: Dict[str, float] = {}
    for k, v in raw.items():
        if k not in _GATE_KEYS:
            continue
        try:
            val = float(v)
        except (TypeError, ValueError):
            continue
        lo, hi = bounds[k]
        out[k] = max(lo, min(hi, val))
    return out


def resolve_gate(
    key: str,
    gate_overrides: Any,
    settings: Dict[str, Any],
    engine_default: float,
) -> float:
    """Return the effective threshold for ``key``.

    Priority:
      1. ``gate_overrides`` JSON (if user explicitly overrode this gate in the UI)
      2. The existing column on trading_settings (if this gate has one)
      3. The engine-level module default passed in as ``engine_default``
    """
    if isinstance(gate_overrides, dict) and key in gate_overrides:
        try:
            return float(gate_overrides[key])
        except (TypeError, ValueError):
            pass
    gate = next((g for g in GATES if g["key"] == key), None)
    if gate and gate.get("settings_field"):
        val = settings.get(gate["settings_field"])
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return float(engine_default)
