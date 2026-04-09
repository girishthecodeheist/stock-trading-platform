"""Seed data generator - creates instruments and realistic OHLCV data for all asset types."""

import random
import math
from datetime import datetime, timedelta
from typing import List, Dict, Any

# NIFTY 50 stocks with approximate prices
NIFTY_50 = [
    {"symbol": "NSE:RELIANCE-EQ", "name": "Reliance Industries", "price": 2450, "segment": "EQUITY"},
    {"symbol": "NSE:TCS-EQ", "name": "Tata Consultancy", "price": 3900, "segment": "EQUITY"},
    {"symbol": "NSE:HDFCBANK-EQ", "name": "HDFC Bank", "price": 1650, "segment": "EQUITY"},
    {"symbol": "NSE:INFY-EQ", "name": "Infosys", "price": 1480, "segment": "EQUITY"},
    {"symbol": "NSE:ICICIBANK-EQ", "name": "ICICI Bank", "price": 1280, "segment": "EQUITY"},
    {"symbol": "NSE:HINDUNILVR-EQ", "name": "Hindustan Unilever", "price": 2350, "segment": "EQUITY"},
    {"symbol": "NSE:ITC-EQ", "name": "ITC Ltd", "price": 460, "segment": "EQUITY"},
    {"symbol": "NSE:SBIN-EQ", "name": "State Bank of India", "price": 810, "segment": "EQUITY"},
    {"symbol": "NSE:BHARTIARTL-EQ", "name": "Bharti Airtel", "price": 1720, "segment": "EQUITY"},
    {"symbol": "NSE:KOTAKBANK-EQ", "name": "Kotak Mahindra Bank", "price": 1850, "segment": "EQUITY"},
    {"symbol": "NSE:LT-EQ", "name": "Larsen & Toubro", "price": 3500, "segment": "EQUITY"},
    {"symbol": "NSE:AXISBANK-EQ", "name": "Axis Bank", "price": 1150, "segment": "EQUITY"},
    {"symbol": "NSE:BAJFINANCE-EQ", "name": "Bajaj Finance", "price": 6800, "segment": "EQUITY"},
    {"symbol": "NSE:ASIANPAINT-EQ", "name": "Asian Paints", "price": 2280, "segment": "EQUITY"},
    {"symbol": "NSE:MARUTI-EQ", "name": "Maruti Suzuki", "price": 12500, "segment": "EQUITY"},
    {"symbol": "NSE:HCLTECH-EQ", "name": "HCL Technologies", "price": 1680, "segment": "EQUITY"},
    {"symbol": "NSE:SUNPHARMA-EQ", "name": "Sun Pharma", "price": 1800, "segment": "EQUITY"},
    {"symbol": "NSE:TITAN-EQ", "name": "Titan Company", "price": 3250, "segment": "EQUITY"},
    {"symbol": "NSE:WIPRO-EQ", "name": "Wipro Ltd", "price": 480, "segment": "EQUITY"},
    {"symbol": "NSE:ULTRACEMCO-EQ", "name": "UltraTech Cement", "price": 11200, "segment": "EQUITY"},
    {"symbol": "NSE:TATAMOTORS-EQ", "name": "Tata Motors", "price": 720, "segment": "EQUITY"},
    {"symbol": "NSE:TATASTEEL-EQ", "name": "Tata Steel", "price": 145, "segment": "EQUITY"},
    {"symbol": "NSE:ADANIENT-EQ", "name": "Adani Enterprises", "price": 2350, "segment": "EQUITY"},
    {"symbol": "NSE:ONGC-EQ", "name": "ONGC", "price": 240, "segment": "EQUITY"},
    {"symbol": "NSE:NTPC-EQ", "name": "NTPC Ltd", "price": 350, "segment": "EQUITY"},
    {"symbol": "NSE:POWERGRID-EQ", "name": "Power Grid Corp", "price": 310, "segment": "EQUITY"},
    {"symbol": "NSE:COALINDIA-EQ", "name": "Coal India", "price": 430, "segment": "EQUITY"},
    {"symbol": "NSE:CIPLA-EQ", "name": "Cipla Ltd", "price": 1520, "segment": "EQUITY"},
    {"symbol": "NSE:DRREDDY-EQ", "name": "Dr Reddy's Labs", "price": 6400, "segment": "EQUITY"},
    {"symbol": "NSE:BPCL-EQ", "name": "BPCL", "price": 620, "segment": "EQUITY"},
]

# Indices
INDICES = [
    {"symbol": "NSE:NIFTY50-INDEX", "name": "NIFTY 50", "price": 22500, "segment": "INDEX"},
    {"symbol": "NSE:BANKNIFTY-INDEX", "name": "Bank NIFTY", "price": 48000, "segment": "INDEX"},
    {"symbol": "NSE:NIFTYIT-INDEX", "name": "NIFTY IT", "price": 34500, "segment": "INDEX"},
    {"symbol": "NSE:FINNIFTY-INDEX", "name": "FIN NIFTY", "price": 20800, "segment": "INDEX"},
]

# Futures
FUTURES = [
    {"symbol": "NSE:NIFTY26MARFUT", "name": "NIFTY MAR FUT", "price": 22520, "segment": "FUTURE", "expiry": "2026-03-26"},
    {"symbol": "NSE:BANKNIFTY26MARFUT", "name": "BANKNIFTY MAR FUT", "price": 48050, "segment": "FUTURE", "expiry": "2026-03-26"},
    {"symbol": "NSE:RELIANCE26MARFUT", "name": "RELIANCE MAR FUT", "price": 2455, "segment": "FUTURE", "expiry": "2026-03-26"},
    {"symbol": "NSE:TCS26MARFUT", "name": "TCS MAR FUT", "price": 3910, "segment": "FUTURE", "expiry": "2026-03-26"},
    {"symbol": "NSE:INFY26MARFUT", "name": "INFY MAR FUT", "price": 1485, "segment": "FUTURE", "expiry": "2026-03-26"},
]

# Options
OPTIONS = [
    {"symbol": "NSE:NIFTY26MAR22500CE", "name": "NIFTY 22500 CE", "price": 180, "segment": "OPTION", "expiry": "2026-03-26", "strike": 22500, "option_type": "CE"},
    {"symbol": "NSE:NIFTY26MAR22500PE", "name": "NIFTY 22500 PE", "price": 150, "segment": "OPTION", "expiry": "2026-03-26", "strike": 22500, "option_type": "PE"},
    {"symbol": "NSE:NIFTY26MAR23000CE", "name": "NIFTY 23000 CE", "price": 45, "segment": "OPTION", "expiry": "2026-03-26", "strike": 23000, "option_type": "CE"},
    {"symbol": "NSE:NIFTY26MAR22000PE", "name": "NIFTY 22000 PE", "price": 35, "segment": "OPTION", "expiry": "2026-03-26", "strike": 22000, "option_type": "PE"},
    {"symbol": "NSE:BANKNIFTY26MAR48000CE", "name": "BANKNIFTY 48000 CE", "price": 320, "segment": "OPTION", "expiry": "2026-03-26", "strike": 48000, "option_type": "CE"},
    {"symbol": "NSE:BANKNIFTY26MAR48000PE", "name": "BANKNIFTY 48000 PE", "price": 280, "segment": "OPTION", "expiry": "2026-03-26", "strike": 48000, "option_type": "PE"},
    {"symbol": "NSE:RELIANCE26MAR2500CE", "name": "RELIANCE 2500 CE", "price": 25, "segment": "OPTION", "expiry": "2026-03-26", "strike": 2500, "option_type": "CE"},
    {"symbol": "NSE:RELIANCE26MAR2400PE", "name": "RELIANCE 2400 PE", "price": 20, "segment": "OPTION", "expiry": "2026-03-26", "strike": 2400, "option_type": "PE"},
]

ALL_INSTRUMENTS = NIFTY_50 + INDICES + FUTURES + OPTIONS


def get_all_instruments() -> List[Dict[str, Any]]:
    return ALL_INSTRUMENTS


def generate_ohlcv_data(base_price: float, days: int = 365, timeframe: str = "1D") -> List[Dict[str, Any]]:
    """Generate realistic OHLCV data using geometric Brownian motion."""
    candles = []
    price = base_price
    dt = datetime.utcnow() - timedelta(days=days)
    
    # Parameters for realistic price movement
    daily_volatility = 0.015  # 1.5% daily volatility
    drift = 0.0002  # slight upward drift
    
    bars_per_day = {
        "1m": 375, "5m": 75, "15m": 25, "1D": 1, "1W": 1/5, "1Y": 1/252
    }
    
    num_bars = int(days * bars_per_day.get(timeframe, 1))
    if timeframe == "1W":
        num_bars = days // 7
    elif timeframe == "1Y":
        num_bars = max(days // 252, 5)
    
    # Adjust volatility for timeframe
    vol_multiplier = {
        "1m": 0.1, "5m": 0.22, "15m": 0.39, "1D": 1.0, "1W": 2.2, "1Y": 15.8
    }
    vol = daily_volatility * vol_multiplier.get(timeframe, 1.0)
    
    # Time delta per bar
    time_deltas = {
        "1m": timedelta(minutes=1),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "1D": timedelta(days=1),
        "1W": timedelta(weeks=1),
        "1Y": timedelta(days=252),
    }
    td = time_deltas.get(timeframe, timedelta(days=1))
    
    for i in range(num_bars):
        # Random walk with drift
        change = random.gauss(drift, vol)
        price = price * (1 + change)
        
        # Generate OHLC from the price
        intra_vol = abs(change) + vol * 0.5
        high = price * (1 + random.uniform(0, intra_vol))
        low = price * (1 - random.uniform(0, intra_vol))
        open_price = price * (1 + random.uniform(-intra_vol * 0.3, intra_vol * 0.3))
        
        # Ensure OHLC consistency
        high = max(high, open_price, price)
        low = min(low, open_price, price)
        
        # Volume with some randomness and trend
        base_vol = int(base_price * 1000)
        vol_factor = random.uniform(0.3, 3.0)
        volume = int(base_vol * vol_factor)
        
        candles.append({
            "timestamp": dt,
            "open": round(open_price, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(price, 2),
            "volume": volume,
        })
        
        dt += td
    
    return candles


def generate_sample_paper_trades(instruments: List[Dict[str, Any]], count: int = 25) -> List[Dict[str, Any]]:
    """Generate sample paper trades for demonstration."""
    trades = []
    now = datetime.utcnow()
    
    for i in range(count):
        inst = random.choice(instruments[:20])  # Use equity stocks
        price = inst["price"]
        side = random.choice(["BUY", "SELL"])
        
        # Random entry in the past
        entry_time = now - timedelta(hours=random.randint(1, 720))
        
        # Determine if trade is closed
        is_closed = random.random() < 0.7  # 70% closed
        
        entry_price = round(price * random.uniform(0.95, 1.05), 2)
        
        if is_closed:
            # Generate exit
            pnl_pct = random.gauss(0.5, 3.0)  # Slightly positive expectancy
            exit_price = round(entry_price * (1 + pnl_pct / 100), 2)
            
            if side == "SELL":
                pnl_pct = -pnl_pct
                
            duration = random.randint(5, 4320)  # 5 min to 3 days
            exit_time = entry_time + timedelta(minutes=duration)
            
            result = "WIN" if pnl_pct > 0 else ("LOSS" if pnl_pct < 0 else "BREAKEVEN")
            exit_reason = random.choice(["Target reached", "Stop loss hit", "Opposite signal", "Manual close"])
            
            trades.append({
                "symbol": inst["symbol"],
                "instrument_type": inst["segment"],
                "timeframe": random.choice(["1m", "5m", "15m", "1D"]),
                "side": side,
                "entry_price": entry_price,
                "entry_time": entry_time,
                "exit_price": exit_price,
                "exit_time": exit_time,
                "quantity": random.choice([1, 5, 10, 25, 50]),
                "stop_loss": round(entry_price * (0.98 if side == "BUY" else 1.02), 2),
                "target": round(entry_price * (1.04 if side == "BUY" else 0.96), 2),
                "status": "CLOSED",
                "result": result,
                "pnl_percent": round(pnl_pct, 2),
                "pnl_amount": round(entry_price * pnl_pct / 100, 2),
                "signal_confidence": round(random.uniform(40, 95), 1),
                "signal_reasons": ["RSI oversold", "MACD bullish crossover", "Price above 50 MA"],
                "duration_minutes": duration,
                "exit_reason": exit_reason,
            })
        else:
            # Open trade
            current_price = round(entry_price * random.uniform(0.97, 1.03), 2)
            pnl_pct = round((current_price - entry_price) / entry_price * 100, 2)
            if side == "SELL":
                pnl_pct = -pnl_pct
                
            trades.append({
                "symbol": inst["symbol"],
                "instrument_type": inst["segment"],
                "timeframe": random.choice(["1m", "5m", "15m", "1D"]),
                "side": side,
                "entry_price": entry_price,
                "entry_time": entry_time,
                "exit_price": None,
                "exit_time": None,
                "quantity": random.choice([1, 5, 10, 25, 50]),
                "stop_loss": round(entry_price * (0.98 if side == "BUY" else 1.02), 2),
                "target": round(entry_price * (1.04 if side == "BUY" else 0.96), 2),
                "status": "OPEN",
                "result": None,
                "pnl_percent": pnl_pct,
                "pnl_amount": round(entry_price * pnl_pct / 100, 2),
                "signal_confidence": round(random.uniform(40, 95), 1),
                "signal_reasons": ["Volume spike", "Price near support", "Stochastic oversold"],
                "duration_minutes": None,
                "exit_reason": None,
            })
    
    return trades
