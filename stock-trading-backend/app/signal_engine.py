"""Unified signal engine - combines technical, fundamental, and sentiment analysis.

Weights: Technical 40%, Fundamental 35%, Sentiment 25%
Outputs: Signal, confidence, PUT/CALL recommendation for F&O
"""

from typing import Any, Dict, List, Optional


def generate_signal(
    indicators: Dict[str, Any],
    fundamental: Optional[Dict[str, Any]] = None,
    sentiment: Optional[Dict[str, Any]] = None,
    instrument_type: str = "EQUITY",
) -> Dict[str, Any]:
    """Generate a comprehensive trading signal combining all analysis types."""
    reasons: List[str] = []
    current_price = indicators.get("current_price", 0)
    if not current_price:
        return _empty_signal()

    # --- TECHNICAL ANALYSIS (weight: 40%) ---
    tech_score, tech_reasons = _analyze_technical(indicators, current_price)
    reasons.extend(tech_reasons)

    # --- FUNDAMENTAL ANALYSIS (weight: 35%) ---
    fund_score = 0.0
    fund_reasons: List[str] = []
    if fundamental and fundamental.get("fundamental_score") is not None:
        fund_score = fundamental["fundamental_score"]
        fund_reasons = _analyze_fundamental(fundamental)
        reasons.extend(fund_reasons)

    # --- SENTIMENT ANALYSIS (weight: 25%) ---
    sent_score = 0.0
    sent_reasons: List[str] = []
    if sentiment and sentiment.get("sentiment_score") is not None:
        sent_score = sentiment["sentiment_score"]
        sent_reasons = _analyze_sentiment(sentiment)
        reasons.extend(sent_reasons)

    # --- WEIGHTED COMBINATION ---
    has_fundamental = fundamental is not None and fundamental.get("pe_ratio") is not None
    has_sentiment = sentiment is not None and sentiment.get("headline_count", 0) > 0

    if has_fundamental and has_sentiment:
        combined_score = tech_score * 0.40 + fund_score * 0.35 + sent_score * 0.25
        weight_desc = "Technical 40% + Fundamental 35% + Sentiment 25%"
    elif has_fundamental:
        combined_score = tech_score * 0.55 + fund_score * 0.45
        weight_desc = "Technical 55% + Fundamental 45%"
    elif has_sentiment:
        combined_score = tech_score * 0.65 + sent_score * 0.35
        weight_desc = "Technical 65% + Sentiment 35%"
    else:
        combined_score = tech_score
        weight_desc = "Technical 100%"

    combined_score = max(-100, min(100, combined_score))
    signal = _classify(combined_score)
    confidence = min(abs(combined_score), 100)

    # --- PUT/CALL for F&O ---
    fno_recommendation = None
    if instrument_type in ("OPTION", "FUTURE", "INDEX"):
        fno_recommendation = _get_fno_recommendation(combined_score, confidence)

    # --- Stop Loss & Targets (v4: tighter, realistic for intraday) ---
    atr = indicators.get("atr")
    stop_loss, target_1, target_2, target_3 = None, None, None, None
    if atr and current_price:
        # v4: Use tighter ATR multipliers for intraday trading
        # SL = 0.75x ATR (was 1.5x), Target = 0.5x/1.0x/1.5x ATR (was 1.0x/2.0x/3.0x)
        # Also cap SL to max 1% of price and targets to max 2% of price
        sl_distance = min(0.75 * atr, current_price * 0.01)
        t1_distance = min(0.5 * atr, current_price * 0.01)
        t2_distance = min(1.0 * atr, current_price * 0.015)
        t3_distance = min(1.5 * atr, current_price * 0.02)

        # Cap targets using support/resistance if available
        support = indicators.get("support_1")
        resistance = indicators.get("resistance_1")

        if combined_score > 0:
            stop_loss = round(current_price - sl_distance, 2)
            target_1 = round(current_price + t1_distance, 2)
            target_2 = round(current_price + t2_distance, 2)
            target_3 = round(current_price + t3_distance, 2)
            # Cap target to resistance if available
            if resistance and resistance > current_price:
                target_1 = round(min(target_1, resistance), 2)
                target_2 = round(min(target_2, resistance * 1.005), 2)
                target_3 = round(min(target_3, resistance * 1.01), 2)
            # Don't set SL below support
            if support and support > 0:
                stop_loss = round(max(stop_loss, support * 0.998), 2)
        elif combined_score < 0:
            stop_loss = round(current_price + sl_distance, 2)
            target_1 = round(current_price - t1_distance, 2)
            target_2 = round(current_price - t2_distance, 2)
            target_3 = round(current_price - t3_distance, 2)
            # Cap target to support if available
            if support and support < current_price:
                target_1 = round(max(target_1, support), 2)
                target_2 = round(max(target_2, support * 0.995), 2)
                target_3 = round(max(target_3, support * 0.99), 2)
            # Don't set SL above resistance
            if resistance and resistance > 0:
                stop_loss = round(min(stop_loss, resistance * 1.002), 2)

    return {
        "signal": signal,
        "confidence": round(confidence, 1),
        "score": round(combined_score, 2),
        "reasons": reasons,
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "target_3": target_3,
        "entry_price": current_price,
        "technical_score": round(tech_score, 2),
        "fundamental_score": round(fund_score, 2),
        "sentiment_score": round(sent_score, 2),
        "weight_description": weight_desc,
        "fno_recommendation": fno_recommendation,
        "instrument_type": instrument_type,
    }


def _analyze_technical(indicators: Dict[str, Any], current_price: float) -> tuple:
    """Analyze technical indicators and return (score, reasons)."""
    score = 0.0
    reasons = []

    # RSI Analysis (weight: 15%)
    rsi = indicators.get("rsi")
    if rsi is not None:
        if rsi < 30:
            score += 15
            reasons.append(f"RSI at {rsi:.1f} - Oversold territory (below 30)")
        elif rsi < 40:
            score += 8
            reasons.append(f"RSI at {rsi:.1f} - Approaching oversold")
        elif rsi > 70:
            score -= 15
            reasons.append(f"RSI at {rsi:.1f} - Overbought territory (above 70)")
        elif rsi > 60:
            score -= 8
            reasons.append(f"RSI at {rsi:.1f} - Approaching overbought")

    # MACD Analysis (weight: 15%)
    macd_line = indicators.get("macd_line")
    macd_signal = indicators.get("macd_signal")
    macd_hist = indicators.get("macd_hist")
    if all(v is not None for v in [macd_line, macd_signal, macd_hist]):
        if macd_line > macd_signal and macd_hist > 0:
            score += 15
            reasons.append("MACD bullish crossover - histogram positive")
        elif macd_line > macd_signal:
            score += 8
            reasons.append("MACD above signal line")
        elif macd_line < macd_signal and macd_hist < 0:
            score -= 15
            reasons.append("MACD bearish crossover - histogram negative")
        elif macd_line < macd_signal:
            score -= 8
            reasons.append("MACD below signal line")

    # Moving Average Analysis (weight: 15%)
    sma_20 = indicators.get("sma_20")
    sma_50 = indicators.get("sma_50")
    sma_200 = indicators.get("sma_200")
    if sma_20 and sma_50 and sma_200:
        if current_price > sma_20 > sma_50 > sma_200:
            score += 15
            reasons.append(f"Strong uptrend: Price > SMA20 > SMA50 > SMA200")
        elif current_price < sma_20 < sma_50 < sma_200:
            score -= 15
            reasons.append(f"Strong downtrend: Price < SMA20 < SMA50 < SMA200")
        elif current_price > sma_50:
            score += 7
            reasons.append(f"Price above 50-day MA ({sma_50})")
        elif current_price < sma_50:
            score -= 7
            reasons.append(f"Price below 50-day MA ({sma_50})")

    # Supertrend (weight: 8%)
    st_dir = indicators.get("supertrend_direction")
    if st_dir == "BULLISH":
        score += 8
        reasons.append("Supertrend indicates BULLISH trend")
    elif st_dir == "BEARISH":
        score -= 8
        reasons.append("Supertrend indicates BEARISH trend")

    # ADX / Trend Strength (weight: 5%)
    adx = indicators.get("adx")
    di_plus = indicators.get("di_plus")
    di_minus = indicators.get("di_minus")
    if adx is not None and di_plus is not None and di_minus is not None:
        if adx > 25:
            if di_plus > di_minus:
                score += 5
                reasons.append(f"Strong bullish trend (ADX={adx:.0f}, DI+={di_plus:.0f} > DI-={di_minus:.0f})")
            else:
                score -= 5
                reasons.append(f"Strong bearish trend (ADX={adx:.0f}, DI-={di_minus:.0f} > DI+={di_plus:.0f})")

    # Ichimoku Cloud (weight: 5%)
    cloud = indicators.get("ichimoku_cloud")
    if cloud == "ABOVE":
        score += 5
        reasons.append("Price above Ichimoku Cloud - bullish")
    elif cloud == "BELOW":
        score -= 5
        reasons.append("Price below Ichimoku Cloud - bearish")

    # Stochastic (weight: 5%)
    stoch_k = indicators.get("stoch_k")
    stoch_d = indicators.get("stoch_d")
    if stoch_k is not None and stoch_d is not None:
        if stoch_k < 20 and stoch_d < 20:
            score += 5
            reasons.append(f"Stochastic oversold (%K={stoch_k:.0f}, %D={stoch_d:.0f})")
        elif stoch_k > 80 and stoch_d > 80:
            score -= 5
            reasons.append(f"Stochastic overbought (%K={stoch_k:.0f}, %D={stoch_d:.0f})")

    # Volume Analysis (weight: 8%)
    volume_ratio = indicators.get("volume_ratio")
    if volume_ratio is not None:
        if volume_ratio >= 2.0:
            if score > 0:
                score += 8
                reasons.append(f"Volume {volume_ratio:.1f}x above average - confirms bullish momentum")
            elif score < 0:
                score -= 8
                reasons.append(f"Volume {volume_ratio:.1f}x above average - confirms bearish pressure")
        elif volume_ratio >= 1.5:
            if score > 0:
                score += 4
            elif score < 0:
                score -= 4
        elif volume_ratio < 0.5:
            # Gap 8: very low volume — price action is not well-confirmed,
            # discount the signal aggressively.
            score *= 0.7
            reasons.append(
                f"Volume {volume_ratio:.1f}x below average - weak conviction, score reduced"
            )
        elif volume_ratio < 0.7:
            # Gap 8: mildly low volume — smaller haircut.
            score *= 0.85
            reasons.append(
                f"Volume {volume_ratio:.1f}x below average - reduced conviction"
            )

    # Williams %R (weight: 3%)
    wr = indicators.get("williams_r")
    if wr is not None:
        if wr < -80:
            score += 3
            reasons.append(f"Williams %R at {wr:.0f} - oversold")
        elif wr > -20:
            score -= 3
            reasons.append(f"Williams %R at {wr:.0f} - overbought")

    # CCI (weight: 3%)
    cci = indicators.get("cci")
    if cci is not None:
        if cci < -100:
            score += 3
            reasons.append(f"CCI at {cci:.0f} - oversold territory")
        elif cci > 100:
            score -= 3
            reasons.append(f"CCI at {cci:.0f} - overbought territory")

    # MFI (weight: 3%)
    mfi = indicators.get("mfi")
    if mfi is not None:
        if mfi < 20:
            score += 3
            reasons.append(f"MFI at {mfi:.0f} - money flow oversold")
        elif mfi > 80:
            score -= 3
            reasons.append(f"MFI at {mfi:.0f} - money flow overbought")

    # OBV Trend (weight: 3%)
    obv_trend = indicators.get("obv_trend")
    if obv_trend == "BULLISH":
        score += 3
        reasons.append("OBV trend BULLISH - accumulation")
    elif obv_trend == "BEARISH":
        score -= 3
        reasons.append("OBV trend BEARISH - distribution")

    # Bollinger Bands (weight: 3%)
    bb_pct_b = indicators.get("bb_pct_b")
    if bb_pct_b is not None:
        if bb_pct_b <= 0:
            score += 3
            reasons.append("Price at/below lower Bollinger Band - potential reversal up")
        elif bb_pct_b >= 1:
            score -= 3
            reasons.append("Price at/above upper Bollinger Band - potential pullback")

    # VWAP (weight: 3%)
    vwap = indicators.get("vwap")
    if vwap is not None and current_price:
        if current_price > vwap * 1.02:
            score += 2
        elif current_price < vwap * 0.98:
            score -= 2

    # Candlestick Patterns (weight: up to 10%)
    patterns = indicators.get("candlestick_patterns", [])
    pattern_score = 0.0
    for p in patterns:
        strength_mult = 1.0
        if p.get("strength") == "STRONG":
            strength_mult = 1.5
        elif p.get("strength") == "WEAK":
            strength_mult = 0.5
        if p.get("type") == "BULLISH":
            pattern_score += 3 * strength_mult
        elif p.get("type") == "BEARISH":
            pattern_score -= 3 * strength_mult
    pattern_score = max(-10, min(10, pattern_score))
    if pattern_score != 0:
        score += pattern_score
        pattern_names = [p["name"] for p in patterns if p.get("type") != "NEUTRAL"]
        if pattern_names:
            direction = "Bullish" if pattern_score > 0 else "Bearish"
            reasons.append(f"{direction} candlestick patterns: {', '.join(pattern_names)}")

    # Swing Points (weight: 3%)
    swing_high = indicators.get("swing_high")
    swing_low = indicators.get("swing_low")
    if swing_high and swing_low and current_price:
        swing_range = swing_high - swing_low
        if swing_range > 0:
            position = (current_price - swing_low) / swing_range
            if position > 0.9:
                score -= 3
                reasons.append(f"Price near swing high ({swing_high}) - potential resistance")
            elif position < 0.1:
                score += 3
                reasons.append(f"Price near swing low ({swing_low}) - potential support")

    # Trend Strength (weight: 3%)
    trend = indicators.get("trend_strength")
    if trend == "STRONG_UPTREND":
        score += 3
        reasons.append("Strong uptrend confirmed by price action")
    elif trend == "STRONG_DOWNTREND":
        score -= 3
        reasons.append("Strong downtrend confirmed by price action")

    # Guppy GMMA (weight: 5%)
    guppy = indicators.get("guppy_signal")
    if guppy == "BULLISH":
        score += 5
        spread = indicators.get("guppy_spread", 0)
        reasons.append(f"Guppy GMMA bullish (spread {spread:+.2f}%) - short EMAs above long EMAs")
    elif guppy == "BEARISH":
        score -= 5
        spread = indicators.get("guppy_spread", 0)
        reasons.append(f"Guppy GMMA bearish (spread {spread:+.2f}%) - short EMAs below long EMAs")
    elif guppy == "COMPRESSION":
        reasons.append("Guppy GMMA compression - trend change likely, wait for breakout")

    return score, reasons


def _analyze_fundamental(data: Dict[str, Any]) -> List[str]:
    """Generate fundamental analysis reasons."""
    reasons = []
    fund_signal = data.get("fundamental_signal", "NEUTRAL")
    fund_score = data.get("fundamental_score", 0)

    pe = data.get("pe_ratio")
    if pe is not None:
        if pe < 15:
            reasons.append(f"P/E ratio {pe:.1f} - attractively valued")
        elif pe > 35:
            reasons.append(f"P/E ratio {pe:.1f} - expensive valuation")
        else:
            reasons.append(f"P/E ratio {pe:.1f}")

    roe = data.get("roe")
    if roe is not None:
        pct = roe * 100 if abs(roe) < 1 else roe
        if pct > 15:
            reasons.append(f"ROE {pct:.1f}% - strong profitability")
        elif pct < 5:
            reasons.append(f"ROE {pct:.1f}% - weak profitability")

    de = data.get("debt_to_equity")
    if de is not None:
        if de < 50:
            reasons.append(f"D/E ratio {de:.0f} - low leverage")
        elif de > 150:
            reasons.append(f"D/E ratio {de:.0f} - high leverage risk")

    pm = data.get("profit_margin")
    if pm is not None:
        pct = pm * 100 if abs(pm) < 1 else pm
        if pct > 15:
            reasons.append(f"Profit margin {pct:.1f}% - healthy margins")

    rg = data.get("revenue_growth")
    if rg is not None:
        pct = rg * 100 if abs(rg) < 1 else rg
        if pct > 10:
            reasons.append(f"Revenue growth {pct:.1f}% - growing business")
        elif pct < 0:
            reasons.append(f"Revenue growth {pct:.1f}% - declining revenue")

    rec = data.get("recommendation")
    if rec and rec != "N/A":
        reasons.append(f"Analyst recommendation: {rec.upper()}")

    if not reasons:
        reasons.append(f"Fundamental signal: {fund_signal} (score: {fund_score:.0f})")

    return reasons


def _analyze_sentiment(data: Dict[str, Any]) -> List[str]:
    """Generate sentiment analysis reasons."""
    reasons = []
    classification = data.get("sentiment_classification", "NEUTRAL")
    headline_count = data.get("headline_count", 0)
    avg = data.get("avg_sentiment", 0)
    bullish = data.get("bullish_count", 0)
    bearish = data.get("bearish_count", 0)

    if headline_count > 0:
        reasons.append(
            f"News sentiment: {classification} ({headline_count} headlines, "
            f"{bullish} bullish / {bearish} bearish, avg={avg:.2f})"
        )
    else:
        reasons.append("No recent news headlines available")

    return reasons


def _get_fno_recommendation(score: float, confidence: float) -> Dict[str, Any]:
    """Generate PUT/CALL recommendation for F&O instruments."""
    if score >= 30:
        return {
            "action": "CALL",
            "strength": "STRONG" if score >= 60 else "MODERATE",
            "confidence": confidence,
            "reasoning": "Strong bullish signals suggest buying CALL options",
        }
    elif score >= 10:
        return {
            "action": "CALL",
            "strength": "WEAK",
            "confidence": confidence,
            "reasoning": "Mildly bullish signals - consider CALL with caution",
        }
    elif score <= -30:
        return {
            "action": "PUT",
            "strength": "STRONG" if score <= -60 else "MODERATE",
            "confidence": confidence,
            "reasoning": "Strong bearish signals suggest buying PUT options",
        }
    elif score <= -10:
        return {
            "action": "PUT",
            "strength": "WEAK",
            "confidence": confidence,
            "reasoning": "Mildly bearish signals - consider PUT with caution",
        }
    else:
        return {
            "action": "NEUTRAL",
            "strength": "NONE",
            "confidence": confidence,
            "reasoning": "No clear directional bias - consider straddle/strangle or wait",
        }


def _classify(score: float) -> str:
    if score >= 60:
        return "STRONG BUY"
    elif score >= 30:
        return "BUY"
    elif score >= 10:
        return "WEAK BUY"
    elif score >= -10:
        return "NEUTRAL"
    elif score >= -30:
        return "WEAK SELL"
    elif score >= -60:
        return "SELL"
    else:
        return "STRONG SELL"


def _empty_signal() -> Dict[str, Any]:
    return {
        "signal": "NEUTRAL",
        "confidence": 0,
        "score": 0,
        "reasons": ["Insufficient data"],
        "stop_loss": None,
        "target_1": None,
        "target_2": None,
        "target_3": None,
        "entry_price": 0,
        "technical_score": 0,
        "fundamental_score": 0,
        "sentiment_score": 0,
        "weight_description": "",
        "fno_recommendation": None,
        "instrument_type": "EQUITY",
    }
