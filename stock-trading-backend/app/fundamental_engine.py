"""Fundamental analysis engine - fetches key financial metrics using yfinance."""

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Symbol mapping: Fyers format -> Yahoo Finance format
NSE_TO_YAHOO = {
    "RELIANCE": "RELIANCE.NS",
    "TCS": "TCS.NS",
    "INFY": "INFY.NS",
    "HDFCBANK": "HDFCBANK.NS",
    "ICICIBANK": "ICICIBANK.NS",
    "HINDUNILVR": "HINDUNILVR.NS",
    "ITC": "ITC.NS",
    "SBIN": "SBIN.NS",
    "BHARTIARTL": "BHARTIARTL.NS",
    "KOTAKBANK": "KOTAKBANK.NS",
    "LT": "LT.NS",
    "AXISBANK": "AXISBANK.NS",
    "ASIANPAINT": "ASIANPAINT.NS",
    "MARUTI": "MARUTI.NS",
    "TATAMOTORS": "TATAMOTORS.NS",
    "TATASTEEL": "TATASTEEL.NS",
    "WIPRO": "WIPRO.NS",
    "HCLTECH": "HCLTECH.NS",
    "BAJFINANCE": "BAJFINANCE.NS",
    "SUNPHARMA": "SUNPHARMA.NS",
}


def _fyers_to_yahoo(symbol: str) -> str:
    """Convert Fyers symbol format to Yahoo Finance format."""
    clean = symbol.replace("NSE:", "").replace("BSE:", "").replace("-EQ", "").replace("-FUT", "").strip()
    if clean in NSE_TO_YAHOO:
        return NSE_TO_YAHOO[clean]
    return f"{clean}.NS"


async def get_fundamental_data(symbol: str) -> Dict[str, Any]:
    """Fetch fundamental data for a symbol using yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed, returning empty fundamentals")
        return _empty_fundamentals()

    yahoo_symbol = _fyers_to_yahoo(symbol)
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _fetch_yfinance_data(yahoo_symbol)
        )
        return result
    except Exception as e:
        logger.error(f"Error fetching fundamental data for {symbol}: {e}")
        return _empty_fundamentals()


def _fetch_yfinance_data(yahoo_symbol: str) -> Dict[str, Any]:
    """Synchronous yfinance data fetch (run in executor)."""
    import yfinance as yf

    ticker = yf.Ticker(yahoo_symbol)
    info = {}
    try:
        info = ticker.info or {}
    except Exception:
        pass

    result = {
        "pe_ratio": _safe_get(info, "trailingPE"),
        "forward_pe": _safe_get(info, "forwardPE"),
        "pb_ratio": _safe_get(info, "priceToBook"),
        "eps": _safe_get(info, "trailingEps"),
        "forward_eps": _safe_get(info, "forwardEps"),
        "dividend_yield": _safe_round(_safe_get(info, "dividendYield"), 4),
        "market_cap": _safe_get(info, "marketCap"),
        "enterprise_value": _safe_get(info, "enterpriseValue"),
        "debt_to_equity": _safe_get(info, "debtToEquity"),
        "roe": _safe_round(_safe_get(info, "returnOnEquity"), 4),
        "roa": _safe_round(_safe_get(info, "returnOnAssets"), 4),
        "current_ratio": _safe_get(info, "currentRatio"),
        "quick_ratio": _safe_get(info, "quickRatio"),
        "profit_margin": _safe_round(_safe_get(info, "profitMargins"), 4),
        "operating_margin": _safe_round(_safe_get(info, "operatingMargins"), 4),
        "gross_margin": _safe_round(_safe_get(info, "grossMargins"), 4),
        "revenue": _safe_get(info, "totalRevenue"),
        "revenue_growth": _safe_round(_safe_get(info, "revenueGrowth"), 4),
        "earnings_growth": _safe_round(_safe_get(info, "earningsGrowth"), 4),
        "book_value": _safe_get(info, "bookValue"),
        "beta": _safe_round(_safe_get(info, "beta"), 4),
        "fifty_two_week_high": _safe_get(info, "fiftyTwoWeekHigh"),
        "fifty_two_week_low": _safe_get(info, "fiftyTwoWeekLow"),
        "fifty_day_avg": _safe_get(info, "fiftyDayAverage"),
        "two_hundred_day_avg": _safe_get(info, "twoHundredDayAverage"),
        "sector": info.get("sector", "N/A"),
        "industry": info.get("industry", "N/A"),
        "company_name": info.get("shortName", info.get("longName", "N/A")),
        "recommendation": info.get("recommendationKey", "N/A"),
        "target_mean_price": _safe_get(info, "targetMeanPrice"),
        "target_high_price": _safe_get(info, "targetHighPrice"),
        "target_low_price": _safe_get(info, "targetLowPrice"),
        "number_of_analysts": _safe_get(info, "numberOfAnalystOpinions"),
        "peg_ratio": _safe_get(info, "pegRatio"),
        "price_to_sales": _safe_get(info, "priceToSalesTrailing12Months"),
        "ev_to_ebitda": _safe_get(info, "enterpriseToEbitda"),
        "ev_to_revenue": _safe_get(info, "enterpriseToRevenue"),
        "free_cash_flow": _safe_get(info, "freeCashflow"),
        "operating_cash_flow": _safe_get(info, "operatingCashflow"),
        "total_debt": _safe_get(info, "totalDebt"),
        "total_cash": _safe_get(info, "totalCash"),
        "shares_outstanding": _safe_get(info, "sharesOutstanding"),
    }

    # Compute fundamental score
    result["fundamental_score"] = _compute_fundamental_score(result)
    result["fundamental_signal"] = _score_to_signal(result["fundamental_score"])

    return result


def _compute_fundamental_score(data: Dict[str, Any]) -> float:
    """Compute a normalized fundamental score from -100 to +100."""
    scores = []
    weights = []

    # P/E ratio scoring
    pe = data.get("pe_ratio")
    if pe is not None and pe > 0:
        if pe < 10:
            scores.append(80)
        elif pe < 15:
            scores.append(60)
        elif pe < 20:
            scores.append(40)
        elif pe < 25:
            scores.append(20)
        elif pe < 35:
            scores.append(0)
        elif pe < 50:
            scores.append(-30)
        else:
            scores.append(-60)
        weights.append(15)

    # P/B ratio scoring
    pb = data.get("pb_ratio")
    if pb is not None and pb > 0:
        if pb < 1:
            scores.append(80)
        elif pb < 2:
            scores.append(50)
        elif pb < 3:
            scores.append(20)
        elif pb < 5:
            scores.append(0)
        else:
            scores.append(-40)
        weights.append(10)

    # ROE scoring
    roe = data.get("roe")
    if roe is not None:
        if roe > 0.25:
            scores.append(80)
        elif roe > 0.15:
            scores.append(50)
        elif roe > 0.10:
            scores.append(20)
        elif roe > 0.05:
            scores.append(0)
        else:
            scores.append(-40)
        weights.append(12)

    # ROA scoring
    roa = data.get("roa")
    if roa is not None:
        if roa > 0.15:
            scores.append(80)
        elif roa > 0.10:
            scores.append(50)
        elif roa > 0.05:
            scores.append(20)
        elif roa > 0.02:
            scores.append(0)
        else:
            scores.append(-40)
        weights.append(8)

    # Debt/Equity scoring
    de = data.get("debt_to_equity")
    if de is not None:
        if de < 20:
            scores.append(70)
        elif de < 50:
            scores.append(40)
        elif de < 100:
            scores.append(10)
        elif de < 200:
            scores.append(-20)
        else:
            scores.append(-60)
        weights.append(10)

    # Profit margin
    pm = data.get("profit_margin")
    if pm is not None:
        if pm > 0.20:
            scores.append(80)
        elif pm > 0.10:
            scores.append(50)
        elif pm > 0.05:
            scores.append(20)
        elif pm > 0:
            scores.append(0)
        else:
            scores.append(-60)
        weights.append(10)

    # Revenue growth
    rg = data.get("revenue_growth")
    if rg is not None:
        if rg > 0.20:
            scores.append(80)
        elif rg > 0.10:
            scores.append(50)
        elif rg > 0.05:
            scores.append(20)
        elif rg > 0:
            scores.append(0)
        else:
            scores.append(-40)
        weights.append(10)

    # Earnings growth
    eg = data.get("earnings_growth")
    if eg is not None:
        if eg > 0.20:
            scores.append(80)
        elif eg > 0.10:
            scores.append(50)
        elif eg > 0:
            scores.append(20)
        else:
            scores.append(-40)
        weights.append(10)

    # Current ratio
    cr = data.get("current_ratio")
    if cr is not None:
        if cr > 2.0:
            scores.append(60)
        elif cr > 1.5:
            scores.append(40)
        elif cr > 1.0:
            scores.append(10)
        else:
            scores.append(-50)
        weights.append(8)

    # Beta scoring (lower beta = more stable)
    beta = data.get("beta")
    if beta is not None:
        if 0.8 <= beta <= 1.2:
            scores.append(40)
        elif 0.5 <= beta <= 1.5:
            scores.append(20)
        else:
            scores.append(-20)
        weights.append(7)

    if not scores:
        return 0.0

    total_weight = sum(weights)
    weighted_sum = sum(s * w for s, w in zip(scores, weights))
    return round(weighted_sum / total_weight, 2) if total_weight > 0 else 0.0


def _score_to_signal(score: float) -> str:
    if score >= 50:
        return "STRONG BULLISH"
    elif score >= 25:
        return "BULLISH"
    elif score >= 10:
        return "SLIGHTLY BULLISH"
    elif score >= -10:
        return "NEUTRAL"
    elif score >= -25:
        return "SLIGHTLY BEARISH"
    elif score >= -50:
        return "BEARISH"
    else:
        return "STRONG BEARISH"


def _safe_get(info: dict, key: str) -> Optional[float]:
    val = info.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_round(val: Optional[float], digits: int = 2) -> Optional[float]:
    if val is None:
        return None
    return round(val, digits)


def _empty_fundamentals() -> Dict[str, Any]:
    return {
        "pe_ratio": None, "forward_pe": None, "pb_ratio": None,
        "eps": None, "forward_eps": None, "dividend_yield": None,
        "market_cap": None, "enterprise_value": None,
        "debt_to_equity": None, "roe": None, "roa": None,
        "current_ratio": None, "quick_ratio": None,
        "profit_margin": None, "operating_margin": None, "gross_margin": None,
        "revenue": None, "revenue_growth": None, "earnings_growth": None,
        "book_value": None, "beta": None,
        "fifty_two_week_high": None, "fifty_two_week_low": None,
        "fifty_day_avg": None, "two_hundred_day_avg": None,
        "sector": "N/A", "industry": "N/A", "company_name": "N/A",
        "recommendation": "N/A",
        "target_mean_price": None, "target_high_price": None, "target_low_price": None,
        "number_of_analysts": None,
        "peg_ratio": None, "price_to_sales": None,
        "ev_to_ebitda": None, "ev_to_revenue": None,
        "free_cash_flow": None, "operating_cash_flow": None,
        "total_debt": None, "total_cash": None,
        "shares_outstanding": None,
        "fundamental_score": 0.0, "fundamental_signal": "NEUTRAL",
    }
