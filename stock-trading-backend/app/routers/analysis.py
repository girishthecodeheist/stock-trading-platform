"""Comprehensive analysis router - combines technical, fundamental, and sentiment."""

import json
from datetime import datetime
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import pandas as pd

from app.database import get_db
from app.indicator_engine import compute_all_indicators
from app.signal_engine import generate_signal
from app.fundamental_engine import get_fundamental_data
from app.news_engine import get_news_sentiment
from app import fyers_client

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.get("/comprehensive")
async def comprehensive_analysis(
    symbol: str = Query(..., description="Symbol to analyze (e.g. NSE:RELIANCE-EQ)"),
    timeframe: str = Query("1D", description="Timeframe"),
    instrument_type: str = Query("EQUITY", description="EQUITY, OPTION, FUTURE, INDEX"),
    db: AsyncSession = Depends(get_db),
):
    """Full comprehensive analysis: technical + fundamental + sentiment + PUT/CALL."""
    # 1. Fetch candle data — prefer Fyers live data when connected
    rows = None
    data_source = "database"

    if fyers_client.is_authenticated():
        days_map = {"1m": 7, "5m": 30, "15m": 60, "1D": 365, "1W": 730, "1Y": 365}
        days = days_map.get(timeframe, 365)
        fyers_candles = await fyers_client.get_historical_data_async(symbol, timeframe, days)
        if fyers_candles and len(fyers_candles) >= 10:
            data_source = "fyers_live"
            df = pd.DataFrame(fyers_candles)
            df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float)
            df["volume"] = df["volume"].astype(float)
            rows = True  # flag that we have data

    if rows is None:
        # Fallback to database
        query = text("""
            SELECT timestamp, open, high, low, close, volume
            FROM ohlcv_candles
            WHERE symbol = :symbol AND timeframe = :timeframe
            ORDER BY timestamp ASC
        """)
        result = await db.execute(query, {"symbol": symbol, "timeframe": timeframe})
        db_rows = result.fetchall()

        if not db_rows or len(db_rows) < 10:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "instrument_type": instrument_type,
                "signal": {"signal": "NEUTRAL", "confidence": 0, "reasons": ["Insufficient data"], "score": 0, "technical_score": 0, "fundamental_score": 0, "sentiment_score": 0, "weight_description": "", "fno_recommendation": None, "instrument_type": instrument_type},
                "indicators": {},
                "fundamental": None,
                "sentiment": None,
            }

        df = pd.DataFrame(db_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float)
        df["volume"] = df["volume"].astype(float)

    # 2. Compute technical indicators
    indicators = compute_all_indicators(df)

    # 3. Fetch fundamental data (only for EQUITY)
    fundamental = None
    if instrument_type in ("EQUITY",):
        try:
            fundamental = await get_fundamental_data(symbol)
        except Exception:
            fundamental = None

    # 4. Fetch news sentiment
    sentiment = None
    try:
        sentiment = await get_news_sentiment(symbol)
    except Exception:
        sentiment = None

    # 5. Generate unified signal
    signal = generate_signal(
        indicators=indicators,
        fundamental=fundamental,
        sentiment=sentiment,
        instrument_type=instrument_type,
    )

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "instrument_type": instrument_type,
        "signal": signal,
        "indicators": indicators,
        "fundamental": fundamental,
        "sentiment": sentiment,
        "data_source": data_source,
        "analysis_time": datetime.utcnow().isoformat(),
    }
