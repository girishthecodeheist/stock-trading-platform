"""SQLAlchemy models for the trading platform."""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, JSON, BigInteger
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Instrument(Base):
    __tablename__ = "instruments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(100), unique=True, nullable=False, index=True)
    exchange = Column(String(20), nullable=False, default="NSE")
    segment = Column(String(20), nullable=False, default="EQUITY")
    name = Column(String(200), nullable=False)
    lot_size = Column(Integer, default=1)
    tick_size = Column(Float, default=0.05)
    expiry = Column(String(20), nullable=True)
    strike = Column(Float, nullable=True)
    option_type = Column(String(5), nullable=True)
    is_active = Column(Boolean, default=True)
    approx_price = Column(Float, default=0)


class OHLCVCandle(Base):
    __tablename__ = "ohlcv_candles"
    __table_args__ = (
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(100), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    timeframe = Column(String(10), nullable=False, default="1D")
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Integer, nullable=False, default=0)


class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(100), nullable=False, index=True)
    signal_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    signal_type = Column(String(20), nullable=False)
    entry_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    target_1 = Column(Float, nullable=True)
    target_2 = Column(Float, nullable=True)
    target_3 = Column(Float, nullable=True)
    confidence_score = Column(Float, nullable=True)
    reasoning = Column(JSON, nullable=True)
    status = Column(String(20), default="OPEN")
    exit_price = Column(Float, nullable=True)
    exit_time = Column(DateTime, nullable=True)
    pnl_percent = Column(Float, nullable=True)
    timeframe = Column(String(10), default="1D")
    instrument_type = Column(String(20), default="EQUITY")
    technical_score = Column(Float, nullable=True)
    fundamental_score = Column(Float, nullable=True)
    fundamental_data = Column(JSON, nullable=True)
    sentiment_score = Column(Float, nullable=True)
    sentiment_data = Column(JSON, nullable=True)
    fno_recommendation = Column(JSON, nullable=True)
    strategy = Column(String(50), nullable=True)
    sector = Column(String(50), nullable=True)
    is_auto_generated = Column(Boolean, default=False)


class PaperTrade(Base):
    __tablename__ = "paper_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_ref = Column(String(25), unique=True, nullable=True)
    symbol = Column(String(100), nullable=False, index=True)
    display_symbol = Column(String(30), nullable=True)
    instrument_type = Column(String(20), default="EQUITY")
    timeframe = Column(String(10), default="1D")
    side = Column(String(10), nullable=False)  # BUY or SELL
    direction = Column(String(10), nullable=True)  # LONG or SHORT
    entry_price = Column(Float, nullable=False)
    entry_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    exit_price = Column(Float, nullable=True)
    exit_time = Column(DateTime, nullable=True)
    quantity = Column(Integer, default=1)
    stop_loss = Column(Float, nullable=True)
    sl_percent = Column(Float, nullable=True)
    target = Column(Float, nullable=True)
    target_percent = Column(Float, nullable=True)
    strategy = Column(String(30), nullable=True)
    risk_reward = Column(Float, nullable=True)
    status = Column(String(20), default="OPEN")  # OPEN, CLOSED
    result = Column(String(10), nullable=True)  # WIN, LOSS, BREAKEVEN
    pnl_percent = Column(Float, nullable=True)
    pnl_amount = Column(Float, nullable=True)
    signal_confidence = Column(Float, nullable=True)
    signal_score = Column(Float, nullable=True)
    signal_strength = Column(String(20), nullable=True)
    signal_reasons = Column(JSON, nullable=True)
    indicators_snapshot = Column(JSON, nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    exit_reason = Column(String(50), nullable=True)
    highest_price = Column(Float, nullable=True)
    lowest_price = Column(Float, nullable=True)
    max_runup = Column(Float, nullable=True)
    max_drawdown = Column(Float, nullable=True)
    signal_id = Column(Integer, nullable=True)


class LiveTrade(Base):
    __tablename__ = "live_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_ref = Column(String(25), unique=True, nullable=True)
    signal_id = Column(Integer, nullable=True)
    symbol = Column(String(100), nullable=False, index=True)
    display_symbol = Column(String(30), nullable=True)
    direction = Column(String(10), nullable=False)  # LONG or SHORT
    fyers_order_id = Column(String(100), nullable=True)
    fyers_sl_order_id = Column(String(100), nullable=True)
    fyers_target_order_id = Column(String(100), nullable=True)
    fyers_trade_no = Column(String(100), nullable=True)
    entry_price = Column(Float, nullable=False)
    entry_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    quantity = Column(Integer, nullable=False, default=1)
    product_type = Column(String(20), default="INTRADAY")
    stop_loss = Column(Float, nullable=True)
    sl_percent = Column(Float, nullable=True)
    target_price = Column(Float, nullable=True)
    target_percent = Column(Float, nullable=True)
    strategy = Column(String(30), nullable=True)
    risk_reward = Column(Float, nullable=True)
    signal_score = Column(Float, nullable=True)
    signal_strength = Column(String(20), nullable=True)
    status = Column(String(30), default="OPEN")
    exit_price = Column(Float, nullable=True)
    exit_time = Column(DateTime, nullable=True)
    exit_reason = Column(String(50), nullable=True)
    gross_pnl = Column(Float, nullable=True)
    brokerage = Column(Float, default=0)
    stt = Column(Float, default=0)
    exchange_charges = Column(Float, default=0)
    gst = Column(Float, default=0)
    sebi_charges = Column(Float, default=0)
    stamp_duty = Column(Float, default=0)
    net_pnl = Column(Float, nullable=True)
    highest_price = Column(Float, nullable=True)
    lowest_price = Column(Float, nullable=True)
    max_runup = Column(Float, nullable=True)
    max_drawdown = Column(Float, nullable=True)
    trade_duration_minutes = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class HeatmapSnapshot(Base):
    __tablename__ = "nse_heatmap_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False, index=True)
    display_sym = Column(String(30), nullable=False)
    sector = Column(String(50), nullable=False, index=True)
    ltp = Column(Float, nullable=False)
    change_pct = Column(Float, nullable=False)
    volume = Column(BigInteger, default=0)
    open_price = Column(Float, nullable=True)
    high_price = Column(Float, nullable=True)
    low_price = Column(Float, nullable=True)
    prev_close = Column(Float, nullable=True)
    is_top_mover = Column(Boolean, default=False)
    rank_by_move = Column(Integer, nullable=True)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class TradingSettings(Base):
    __tablename__ = "trading_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_mode = Column(String(10), default="PAPER")
    simulated_capital = Column(Float, default=100000.0)
    default_sl_percent = Column(Float, default=1.5)
    default_target_percent = Column(Float, default=3.0)
    default_quantity = Column(Integer, default=1)
    max_open_trades = Column(Integer, default=5)
    allow_duplicate_symbol = Column(Boolean, default=False)
    day_max_loss_paper = Column(Float, default=-1000.0)
    day_profit_target_paper = Column(Float, default=3000.0)
    trading_halted_paper = Column(Boolean, default=False)
    day_max_loss_live = Column(Float, default=-2000.0)
    day_profit_target_live = Column(Float, default=5000.0)
    trading_halted_live = Column(Boolean, default=False)
    halt_reason = Column(String(200), nullable=True)
    day_limits_reset_at = Column(DateTime, nullable=True)
    scan_frequency_minutes = Column(Integer, default=5)
    top_movers_count = Column(Integer, default=50)
    min_change_pct = Column(Float, default=0.5)
    min_volume = Column(Integer, default=100000)
    auto_trade_enabled = Column(Boolean, default=False)
    auto_quantity_enabled = Column(Boolean, default=True)
    scanner_running = Column(Boolean, default=False)
    max_trades_per_day = Column(Integer, default=50)
    min_net_profit_per_trade = Column(Float, default=1.0)
    min_profit_to_cost_ratio = Column(Float, default=1.0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
