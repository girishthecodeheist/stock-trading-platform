"""Technical indicators engine - computes 22+ indicators from OHLCV data."""

import numpy as np
import pandas as pd
from typing import Any, Dict, Optional


def compute_all_indicators(df: pd.DataFrame) -> Dict[str, Any]:
    """Compute all technical indicators for OHLCV DataFrame."""
    if df is None or len(df) < 2:
        return {}

    result: Dict[str, Any] = {}
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    for period in [5, 10, 20, 50, 100, 200]:
        key = f"sma_{period}"
        if len(close) >= period:
            result[key] = round(float(close.rolling(period).mean().iloc[-1]), 2)
        else:
            result[key] = None

    for period in [9, 12, 20, 26, 50, 200]:
        key = f"ema_{period}"
        if len(close) >= period:
            result[key] = round(float(close.ewm(span=period, adjust=False).mean().iloc[-1]), 2)
        else:
            result[key] = None

    result["rsi"] = _compute_rsi(close, 14)

    macd = _compute_macd(close)
    result["macd_line"] = macd["macd_line"]
    result["macd_signal"] = macd["signal_line"]
    result["macd_hist"] = macd["histogram"]

    if len(volume) >= 20:
        vol_sma = float(volume.rolling(20).mean().iloc[-1])
        result["volume_sma_20"] = round(vol_sma, 0)
        result["volume_ratio"] = round(float(volume.iloc[-1] / vol_sma), 2) if vol_sma > 0 else 1.0
        result["volume_trend"] = "HIGH" if result["volume_ratio"] >= 1.5 else ("LOW" if result["volume_ratio"] < 0.5 else "NORMAL")
    else:
        result["volume_sma_20"] = None
        result["volume_ratio"] = None
        result["volume_trend"] = "UNKNOWN"

    bb = _compute_bollinger_bands(close, 20, 2.0)
    result["bb_upper"] = bb["upper"]
    result["bb_mid"] = bb["mid"]
    result["bb_lower"] = bb["lower"]
    result["bb_width"] = bb["width"]
    result["bb_pct_b"] = bb["pct_b"]

    sr = _compute_support_resistance(high, low, close)
    result.update(sr)

    result["atr"] = _compute_atr(high, low, close, 14)

    stoch = _compute_stochastic(high, low, close)
    result["stoch_k"] = stoch["k"]
    result["stoch_d"] = stoch["d"]

    result["williams_r"] = _compute_williams_r(high, low, close, 14)
    result["cci"] = _compute_cci(high, low, close, 20)

    adx_data = _compute_adx(high, low, close, 14)
    result["adx"] = adx_data["adx"]
    result["di_plus"] = adx_data["di_plus"]
    result["di_minus"] = adx_data["di_minus"]

    result["obv"] = _compute_obv(close, volume)
    result["obv_trend"] = _compute_obv_trend(close, volume)
    result["vwap"] = _compute_vwap(high, low, close, volume)

    ichimoku = _compute_ichimoku(high, low, close)
    result.update(ichimoku)

    result["psar"] = _compute_parabolic_sar(high, low, close)
    result["mfi"] = _compute_mfi(high, low, close, volume, 14)
    result["roc"] = _compute_roc(close, 12)
    result["cmf"] = _compute_cmf(high, low, close, volume, 20)

    donchian = _compute_donchian(high, low, 20)
    result["donchian_upper"] = donchian["upper"]
    result["donchian_lower"] = donchian["lower"]
    result["donchian_mid"] = donchian["mid"]

    keltner = _compute_keltner(high, low, close, 20, 2.0)
    result["keltner_upper"] = keltner["upper"]
    result["keltner_mid"] = keltner["mid"]
    result["keltner_lower"] = keltner["lower"]

    result["trix"] = _compute_trix(close, 15)
    result["force_index"] = _compute_force_index(close, volume, 13)

    elder = _compute_elder_ray(high, low, close, 13)
    result["elder_bull"] = elder["bull_power"]
    result["elder_bear"] = elder["bear_power"]

    st = _compute_supertrend(high, low, close, 10, 3.0)
    result["supertrend"] = st["value"]
    result["supertrend_direction"] = st["direction"]

    # Guppy Multiple Moving Average (GMMA)
    gmma = _compute_guppy_gmma(close)
    result.update(gmma)

    # Candlestick patterns
    patterns = _detect_candlestick_patterns(df)
    result["candlestick_patterns"] = patterns

    # Swing highs/lows
    swings = _detect_swing_points(high, low, close)
    result.update(swings)

    # Price action context
    pa = _price_action_context(high, low, close, volume)
    result.update(pa)

    result["current_price"] = round(float(close.iloc[-1]), 2)
    result["prev_close"] = round(float(close.iloc[-2]), 2) if len(close) >= 2 else None
    result["price_change"] = round(float(close.iloc[-1] - close.iloc[-2]), 2) if len(close) >= 2 else 0
    result["price_change_pct"] = round(float((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100), 2) if len(close) >= 2 else 0

    lookback_52w = min(252, len(high))
    if lookback_52w >= 20:
        result["high_52w"] = round(float(high.tail(lookback_52w).max()), 2)
        result["low_52w"] = round(float(low.tail(lookback_52w).min()), 2)
    else:
        result["high_52w"] = None
        result["low_52w"] = None

    return result


def _compute_rsi(close, period=14):
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    val = rsi.iloc[-1]
    return round(float(val), 2) if not np.isnan(val) else None


def _compute_macd(close, fast=12, slow=26, signal=9):
    if len(close) < slow + signal:
        return {"macd_line": None, "signal_line": None, "histogram": None}
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return {
        "macd_line": round(float(macd_line.iloc[-1]), 4),
        "signal_line": round(float(signal_line.iloc[-1]), 4),
        "histogram": round(float(histogram.iloc[-1]), 4),
    }


def _compute_bollinger_bands(close, period=20, std_dev=2.0):
    if len(close) < period:
        return {"upper": None, "mid": None, "lower": None, "width": None, "pct_b": None}
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    width_val = ((upper.iloc[-1] - lower.iloc[-1]) / mid.iloc[-1] * 100) if mid.iloc[-1] != 0 else 0
    denom = upper.iloc[-1] - lower.iloc[-1]
    pct_b_val = ((close.iloc[-1] - lower.iloc[-1]) / denom) if denom != 0 else 0.5
    return {
        "upper": round(float(upper.iloc[-1]), 2),
        "mid": round(float(mid.iloc[-1]), 2),
        "lower": round(float(lower.iloc[-1]), 2),
        "width": round(float(width_val), 2),
        "pct_b": round(float(pct_b_val), 4),
    }


def _compute_support_resistance(high, low, close):
    result = {}
    if len(high) >= 2:
        h, l, c = float(high.iloc[-2]), float(low.iloc[-2]), float(close.iloc[-2])
        pivot = (h + l + c) / 3
        result["pivot"] = round(pivot, 2)
        result["support_1"] = round(2 * pivot - h, 2)
        result["resistance_1"] = round(2 * pivot - l, 2)
        result["support_2"] = round(pivot - (h - l), 2)
        result["resistance_2"] = round(pivot + (h - l), 2)
        result["support_3"] = round(l - 2 * (h - pivot), 2)
        result["resistance_3"] = round(h + 2 * (pivot - l), 2)
    else:
        for k in ["pivot", "support_1", "resistance_1", "support_2", "resistance_2", "support_3", "resistance_3"]:
            result[k] = None
    lookback = min(20, len(high))
    if lookback >= 5:
        result["recent_high"] = round(float(high.tail(lookback).max()), 2)
        result["recent_low"] = round(float(low.tail(lookback).min()), 2)
    else:
        result["recent_high"] = None
        result["recent_low"] = None
    return result


def _compute_atr(high, low, close, period=14):
    if len(close) < period + 1:
        return None
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, min_periods=period).mean()
    val = atr.iloc[-1]
    return round(float(val), 2) if not np.isnan(val) else None


def _compute_stochastic(high, low, close, k_period=14, d_period=3, smooth=3):
    if len(close) < k_period + smooth:
        return {"k": None, "d": None}
    lowest_low = low.rolling(k_period).min()
    highest_high = high.rolling(k_period).max()
    denom = highest_high - lowest_low
    raw_k = ((close - lowest_low) / denom.replace(0, np.nan)) * 100
    k = raw_k.rolling(smooth).mean()
    d = k.rolling(d_period).mean()
    return {
        "k": round(float(k.iloc[-1]), 2) if not np.isnan(k.iloc[-1]) else None,
        "d": round(float(d.iloc[-1]), 2) if not np.isnan(d.iloc[-1]) else None,
    }


def _compute_williams_r(high, low, close, period=14):
    if len(close) < period:
        return None
    highest = high.rolling(period).max()
    lowest = low.rolling(period).min()
    denom = highest - lowest
    wr = ((highest - close) / denom.replace(0, np.nan)) * -100
    val = wr.iloc[-1]
    return round(float(val), 2) if not np.isnan(val) else None


def _compute_cci(high, low, close, period=20):
    if len(close) < period:
        return None
    tp = (high + low + close) / 3
    sma_tp = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    cci = (tp - sma_tp) / (0.015 * mad.replace(0, np.nan))
    val = cci.iloc[-1]
    return round(float(val), 2) if not np.isnan(val) else None


def _compute_adx(high, low, close, period=14):
    if len(close) < period * 2:
        return {"adx": None, "di_plus": None, "di_minus": None}
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, min_periods=period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, min_periods=period).mean() / atr.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, min_periods=period).mean() / atr.replace(0, np.nan))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1/period, min_periods=period).mean()
    return {
        "adx": round(float(adx.iloc[-1]), 2) if not np.isnan(adx.iloc[-1]) else None,
        "di_plus": round(float(plus_di.iloc[-1]), 2) if not np.isnan(plus_di.iloc[-1]) else None,
        "di_minus": round(float(minus_di.iloc[-1]), 2) if not np.isnan(minus_di.iloc[-1]) else None,
    }


def _compute_obv(close, volume):
    if len(close) < 2:
        return None
    direction = np.sign(close.diff())
    obv = (direction * volume).cumsum()
    return round(float(obv.iloc[-1]), 0)


def _compute_obv_trend(close, volume):
    if len(close) < 22:
        return None
    direction = np.sign(close.diff())
    obv = (direction * volume).cumsum()
    obv_sma = obv.rolling(20).mean()
    return "BULLISH" if obv.iloc[-1] > obv_sma.iloc[-1] else "BEARISH"


def _compute_vwap(high, low, close, volume):
    if len(close) < 2 or volume.sum() == 0:
        return None
    tp = (high + low + close) / 3
    vwap = (tp * volume).cumsum() / volume.cumsum().replace(0, np.nan)
    val = vwap.iloc[-1]
    return round(float(val), 2) if not np.isnan(val) else None


def _compute_ichimoku(high, low, close, tenkan=9, kijun=26, senkou_b_period=52):
    result = {}
    if len(close) >= tenkan:
        tenkan_val = (high.rolling(tenkan).max() + low.rolling(tenkan).min()) / 2
        result["ichimoku_tenkan"] = round(float(tenkan_val.iloc[-1]), 2)
    else:
        result["ichimoku_tenkan"] = None
    if len(close) >= kijun:
        kijun_val = (high.rolling(kijun).max() + low.rolling(kijun).min()) / 2
        result["ichimoku_kijun"] = round(float(kijun_val.iloc[-1]), 2)
    else:
        result["ichimoku_kijun"] = None
    if result["ichimoku_tenkan"] is not None and result["ichimoku_kijun"] is not None:
        result["ichimoku_senkou_a"] = round((result["ichimoku_tenkan"] + result["ichimoku_kijun"]) / 2, 2)
    else:
        result["ichimoku_senkou_a"] = None
    if len(close) >= senkou_b_period:
        sb = (high.rolling(senkou_b_period).max() + low.rolling(senkou_b_period).min()) / 2
        result["ichimoku_senkou_b"] = round(float(sb.iloc[-1]), 2)
    else:
        result["ichimoku_senkou_b"] = None
    cp = float(close.iloc[-1])
    sa = result.get("ichimoku_senkou_a")
    sb_val = result.get("ichimoku_senkou_b")
    if sa is not None and sb_val is not None:
        cloud_top = max(sa, sb_val)
        cloud_bottom = min(sa, sb_val)
        if cp > cloud_top:
            result["ichimoku_cloud"] = "ABOVE"
        elif cp < cloud_bottom:
            result["ichimoku_cloud"] = "BELOW"
        else:
            result["ichimoku_cloud"] = "INSIDE"
    else:
        result["ichimoku_cloud"] = None
    return result


def _compute_parabolic_sar(high, low, close, af_start=0.02, af_step=0.02, af_max=0.2):
    if len(close) < 5:
        return None
    h = high.values.astype(float)
    l = low.values.astype(float)
    n = len(h)
    psar = np.zeros(n)
    af = af_start
    bull = True
    ep = h[0]
    psar[0] = l[0]
    for i in range(1, n):
        if bull:
            psar[i] = psar[i-1] + af * (ep - psar[i-1])
            psar[i] = min(psar[i], l[i-1])
            if i >= 2:
                psar[i] = min(psar[i], l[i-2])
            if l[i] < psar[i]:
                bull = False
                psar[i] = ep
                ep = l[i]
                af = af_start
            else:
                if h[i] > ep:
                    ep = h[i]
                    af = min(af + af_step, af_max)
        else:
            psar[i] = psar[i-1] + af * (ep - psar[i-1])
            psar[i] = max(psar[i], h[i-1])
            if i >= 2:
                psar[i] = max(psar[i], h[i-2])
            if h[i] > psar[i]:
                bull = True
                psar[i] = ep
                ep = h[i]
                af = af_start
            else:
                if l[i] < ep:
                    ep = l[i]
                    af = min(af + af_step, af_max)
    return round(float(psar[-1]), 2)


def _compute_mfi(high, low, close, volume, period=14):
    if len(close) < period + 1:
        return None
    tp = (high + low + close) / 3
    mf = tp * volume
    tp_diff = tp.diff()
    pos_mf = mf.where(tp_diff > 0, 0.0).rolling(period).sum()
    neg_mf = mf.where(tp_diff <= 0, 0.0).rolling(period).sum()
    mfi = 100 - (100 / (1 + pos_mf / neg_mf.replace(0, np.nan)))
    val = mfi.iloc[-1]
    return round(float(val), 2) if not np.isnan(val) else None


def _compute_roc(close, period=12):
    if len(close) < period + 1:
        return None
    prev = close.shift(period)
    roc = ((close - prev) / prev.replace(0, np.nan)) * 100
    val = roc.iloc[-1]
    return round(float(val), 2) if not np.isnan(val) else None


def _compute_cmf(high, low, close, volume, period=20):
    if len(close) < period:
        return None
    denom = high - low
    clv = ((close - low) - (high - close)) / denom.replace(0, np.nan)
    clv = clv.fillna(0)
    cmf = (clv * volume).rolling(period).sum() / volume.rolling(period).sum().replace(0, np.nan)
    val = cmf.iloc[-1]
    return round(float(val), 4) if not np.isnan(val) else None


def _compute_donchian(high, low, period=20):
    if len(high) < period:
        return {"upper": None, "lower": None, "mid": None}
    upper = high.rolling(period).max()
    lower = low.rolling(period).min()
    mid = (upper + lower) / 2
    return {
        "upper": round(float(upper.iloc[-1]), 2),
        "lower": round(float(lower.iloc[-1]), 2),
        "mid": round(float(mid.iloc[-1]), 2),
    }


def _compute_keltner(high, low, close, period=20, mult=2.0):
    if len(close) < period + 14:
        return {"upper": None, "mid": None, "lower": None}
    mid = close.ewm(span=period, adjust=False).mean()
    atr = _compute_atr(high, low, close, period)
    if atr is None:
        return {"upper": None, "mid": round(float(mid.iloc[-1]), 2), "lower": None}
    return {
        "upper": round(float(mid.iloc[-1]) + mult * atr, 2),
        "mid": round(float(mid.iloc[-1]), 2),
        "lower": round(float(mid.iloc[-1]) - mult * atr, 2),
    }


def _compute_trix(close, period=15):
    if len(close) < period * 3 + 1:
        return None
    ema1 = close.ewm(span=period, adjust=False).mean()
    ema2 = ema1.ewm(span=period, adjust=False).mean()
    ema3 = ema2.ewm(span=period, adjust=False).mean()
    trix = ema3.pct_change() * 100
    val = trix.iloc[-1]
    return round(float(val), 4) if not np.isnan(val) else None


def _compute_force_index(close, volume, period=13):
    if len(close) < period + 1:
        return None
    fi = close.diff() * volume
    fi_smoothed = fi.ewm(span=period, adjust=False).mean()
    val = fi_smoothed.iloc[-1]
    return round(float(val), 0) if not np.isnan(val) else None


def _compute_elder_ray(high, low, close, period=13):
    if len(close) < period:
        return {"bull_power": None, "bear_power": None}
    ema = close.ewm(span=period, adjust=False).mean()
    bull = high - ema
    bear = low - ema
    return {
        "bull_power": round(float(bull.iloc[-1]), 2),
        "bear_power": round(float(bear.iloc[-1]), 2),
    }


def _compute_supertrend(high, low, close, period=10, multiplier=3.0):
    if len(close) < period + 1:
        return {"value": None, "direction": None}
    h = high.values.astype(float)
    l = low.values.astype(float)
    c = close.values.astype(float)
    n = len(c)
    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    atr = pd.Series(tr).ewm(alpha=1/period, min_periods=period).mean().values
    hl2 = (h + l) / 2
    upper_band = hl2 + multiplier * atr
    lower_band = hl2 - multiplier * atr
    supertrend = np.zeros(n)
    direction = np.ones(n)
    supertrend[0] = upper_band[0]
    direction[0] = -1
    for i in range(1, n):
        if c[i] > upper_band[i-1]:
            direction[i] = 1
        elif c[i] < lower_band[i-1]:
            direction[i] = -1
        else:
            direction[i] = direction[i-1]
        if direction[i] == 1:
            supertrend[i] = max(lower_band[i], supertrend[i-1]) if direction[i-1] == 1 else lower_band[i]
        else:
            supertrend[i] = min(upper_band[i], supertrend[i-1]) if direction[i-1] == -1 else upper_band[i]
    return {
        "value": round(float(supertrend[-1]), 2),
        "direction": "BULLISH" if direction[-1] == 1 else "BEARISH",
    }


def _detect_candlestick_patterns(df: pd.DataFrame) -> list:
    """Detect common candlestick patterns from recent OHLCV data."""
    if len(df) < 5:
        return []
    patterns = []
    o = df["open"].astype(float).values
    h = df["high"].astype(float).values
    l = df["low"].astype(float).values
    c = df["close"].astype(float).values

    def body(i):
        return abs(c[i] - o[i])

    def upper_shadow(i):
        return h[i] - max(o[i], c[i])

    def lower_shadow(i):
        return min(o[i], c[i]) - l[i]

    def total_range(i):
        return h[i] - l[i] if h[i] != l[i] else 0.0001

    i = len(o) - 1  # latest candle
    b = body(i)
    tr = total_range(i)
    us = upper_shadow(i)
    ls = lower_shadow(i)

    # --- Single candle patterns ---
    # Doji
    if b / tr < 0.1:
        patterns.append({"name": "Doji", "type": "NEUTRAL", "strength": "MODERATE"})

    # Hammer (bullish reversal at bottom)
    if ls >= 2 * b and us < b * 0.3 and c[i] > o[i]:
        patterns.append({"name": "Hammer", "type": "BULLISH", "strength": "STRONG"})

    # Inverted Hammer
    if us >= 2 * b and ls < b * 0.3 and c[i] > o[i]:
        patterns.append({"name": "Inverted Hammer", "type": "BULLISH", "strength": "MODERATE"})

    # Hanging Man (bearish reversal at top)
    if ls >= 2 * b and us < b * 0.3 and c[i] < o[i]:
        patterns.append({"name": "Hanging Man", "type": "BEARISH", "strength": "STRONG"})

    # Shooting Star
    if us >= 2 * b and ls < b * 0.3 and c[i] < o[i]:
        patterns.append({"name": "Shooting Star", "type": "BEARISH", "strength": "STRONG"})

    # Marubozu (strong momentum candle)
    if b / tr > 0.9:
        if c[i] > o[i]:
            patterns.append({"name": "Bullish Marubozu", "type": "BULLISH", "strength": "STRONG"})
        else:
            patterns.append({"name": "Bearish Marubozu", "type": "BEARISH", "strength": "STRONG"})

    # Spinning Top
    if 0.1 <= b / tr <= 0.3 and us > b and ls > b:
        patterns.append({"name": "Spinning Top", "type": "NEUTRAL", "strength": "WEAK"})

    # --- Two candle patterns ---
    if len(o) >= 2:
        j = i - 1  # previous candle
        b_prev = body(j)
        # Bullish Engulfing
        if c[j] < o[j] and c[i] > o[i] and o[i] <= c[j] and c[i] >= o[j]:
            patterns.append({"name": "Bullish Engulfing", "type": "BULLISH", "strength": "STRONG"})
        # Bearish Engulfing
        if c[j] > o[j] and c[i] < o[i] and o[i] >= c[j] and c[i] <= o[j]:
            patterns.append({"name": "Bearish Engulfing", "type": "BEARISH", "strength": "STRONG"})
        # Bullish Harami
        if c[j] < o[j] and c[i] > o[i] and o[i] > c[j] and c[i] < o[j] and b < b_prev * 0.6:
            patterns.append({"name": "Bullish Harami", "type": "BULLISH", "strength": "MODERATE"})
        # Bearish Harami
        if c[j] > o[j] and c[i] < o[i] and o[i] < c[j] and c[i] > o[j] and b < b_prev * 0.6:
            patterns.append({"name": "Bearish Harami", "type": "BEARISH", "strength": "MODERATE"})
        # Tweezer Top
        if abs(h[i] - h[j]) / tr < 0.05 and c[j] > o[j] and c[i] < o[i]:
            patterns.append({"name": "Tweezer Top", "type": "BEARISH", "strength": "MODERATE"})
        # Tweezer Bottom
        if abs(l[i] - l[j]) / tr < 0.05 and c[j] < o[j] and c[i] > o[i]:
            patterns.append({"name": "Tweezer Bottom", "type": "BULLISH", "strength": "MODERATE"})
        # Piercing Line
        if c[j] < o[j] and c[i] > o[i] and o[i] < c[j] and c[i] > (o[j] + c[j]) / 2:
            patterns.append({"name": "Piercing Line", "type": "BULLISH", "strength": "MODERATE"})
        # Dark Cloud Cover
        if c[j] > o[j] and c[i] < o[i] and o[i] > c[j] and c[i] < (o[j] + c[j]) / 2:
            patterns.append({"name": "Dark Cloud Cover", "type": "BEARISH", "strength": "MODERATE"})

    # --- Three candle patterns ---
    if len(o) >= 3:
        k = i - 2
        # Morning Star
        if c[k] < o[k] and body(k) > body(i-1) * 2 and c[i] > o[i] and c[i] > (o[k] + c[k]) / 2:
            patterns.append({"name": "Morning Star", "type": "BULLISH", "strength": "STRONG"})
        # Evening Star
        if c[k] > o[k] and body(k) > body(i-1) * 2 and c[i] < o[i] and c[i] < (o[k] + c[k]) / 2:
            patterns.append({"name": "Evening Star", "type": "BEARISH", "strength": "STRONG"})
        # Three White Soldiers
        if all(c[k+x] > o[k+x] for x in range(3)) and c[k+1] > c[k] and c[k+2] > c[k+1]:
            patterns.append({"name": "Three White Soldiers", "type": "BULLISH", "strength": "STRONG"})
        # Three Black Crows
        if all(c[k+x] < o[k+x] for x in range(3)) and c[k+1] < c[k] and c[k+2] < c[k+1]:
            patterns.append({"name": "Three Black Crows", "type": "BEARISH", "strength": "STRONG"})

    return patterns


def _detect_swing_points(high, low, close, lookback=5):
    """Detect recent swing highs and swing lows."""
    result = {}
    n = len(high)
    if n < lookback * 2 + 1:
        result["swing_high"] = None
        result["swing_low"] = None
        result["swing_high_idx"] = None
        result["swing_low_idx"] = None
        return result

    h = high.values.astype(float)
    l = low.values.astype(float)

    swing_high = None
    swing_high_idx = None
    swing_low = None
    swing_low_idx = None

    # Scan backward to find the most recent swing high and swing low
    for idx in range(n - lookback - 1, lookback - 1, -1):
        if swing_high is None:
            if all(h[idx] >= h[idx - j] for j in range(1, lookback + 1)) and \
               all(h[idx] >= h[idx + j] for j in range(1, min(lookback + 1, n - idx))):
                swing_high = round(float(h[idx]), 2)
                swing_high_idx = idx
        if swing_low is None:
            if all(l[idx] <= l[idx - j] for j in range(1, lookback + 1)) and \
               all(l[idx] <= l[idx + j] for j in range(1, min(lookback + 1, n - idx))):
                swing_low = round(float(l[idx]), 2)
                swing_low_idx = idx
        if swing_high is not None and swing_low is not None:
            break

    result["swing_high"] = swing_high
    result["swing_low"] = swing_low
    result["swing_high_bars_ago"] = n - 1 - swing_high_idx if swing_high_idx is not None else None
    result["swing_low_bars_ago"] = n - 1 - swing_low_idx if swing_low_idx is not None else None
    return result


def _price_action_context(high, low, close, volume):
    """Compute price action context: trend strength, consecutive candles, etc."""
    result = {}
    n = len(close)
    if n < 5:
        result["consecutive_green"] = 0
        result["consecutive_red"] = 0
        result["trend_strength"] = "UNKNOWN"
        return result

    c = close.values.astype(float)
    o_vals = high.values.astype(float)  # We don't have open in params, approximate

    # Count consecutive green/red candles
    green_count = 0
    for idx in range(n - 1, 0, -1):
        if c[idx] > c[idx - 1]:
            green_count += 1
        else:
            break
    red_count = 0
    for idx in range(n - 1, 0, -1):
        if c[idx] < c[idx - 1]:
            red_count += 1
        else:
            break

    result["consecutive_green"] = green_count
    result["consecutive_red"] = red_count

    # Trend strength based on recent price action
    if n >= 20:
        sma5 = float(close.rolling(5).mean().iloc[-1])
        sma10 = float(close.rolling(10).mean().iloc[-1])
        sma20 = float(close.rolling(20).mean().iloc[-1])
        cp = float(c[-1])
        if cp > sma5 > sma10 > sma20:
            result["trend_strength"] = "STRONG_UPTREND"
        elif cp > sma10 > sma20:
            result["trend_strength"] = "UPTREND"
        elif cp < sma5 < sma10 < sma20:
            result["trend_strength"] = "STRONG_DOWNTREND"
        elif cp < sma10 < sma20:
            result["trend_strength"] = "DOWNTREND"
        else:
            result["trend_strength"] = "SIDEWAYS"
    else:
        result["trend_strength"] = "UNKNOWN"

    return result


def _compute_guppy_gmma(close):
    """Compute Guppy Multiple Moving Average (GMMA).
    
    Short-term EMAs: 3, 5, 8, 10, 12, 15
    Long-term EMAs: 30, 35, 40, 45, 50, 60
    
    When short-term group is above long-term group = bullish trend.
    When they converge = trend change likely.
    """
    result = {}
    short_periods = [3, 5, 8, 10, 12, 15]
    long_periods = [30, 35, 40, 45, 50, 60]
    
    if len(close) < max(long_periods):
        result["guppy_signal"] = None
        result["guppy_short_avg"] = None
        result["guppy_long_avg"] = None
        result["guppy_spread"] = None
        result["guppy_compression"] = None
        return result
    
    short_emas = []
    for p in short_periods:
        ema_val = float(close.ewm(span=p, adjust=False).mean().iloc[-1])
        short_emas.append(ema_val)
    
    long_emas = []
    for p in long_periods:
        ema_val = float(close.ewm(span=p, adjust=False).mean().iloc[-1])
        long_emas.append(ema_val)
    
    short_avg = sum(short_emas) / len(short_emas)
    long_avg = sum(long_emas) / len(long_emas)
    
    # Spread between short and long group (as % of price)
    cp = float(close.iloc[-1])
    spread_pct = ((short_avg - long_avg) / cp * 100) if cp > 0 else 0
    
    # Compression: how tight are the short-term EMAs (low = compressed = breakout likely)
    short_range = max(short_emas) - min(short_emas)
    short_compression = (short_range / cp * 100) if cp > 0 else 0
    
    # Signal determination
    if short_avg > long_avg and spread_pct > 0.5:
        guppy_signal = "BULLISH"
    elif short_avg < long_avg and spread_pct < -0.5:
        guppy_signal = "BEARISH"
    elif abs(spread_pct) < 0.2:
        guppy_signal = "COMPRESSION"  # Trend change likely
    else:
        guppy_signal = "NEUTRAL"
    
    result["guppy_signal"] = guppy_signal
    result["guppy_short_avg"] = round(short_avg, 2)
    result["guppy_long_avg"] = round(long_avg, 2)
    result["guppy_spread"] = round(spread_pct, 2)
    result["guppy_compression"] = round(short_compression, 4)
    
    return result
