"""Stock Trading Platform - FastAPI Backend."""

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.database import init_db, async_session_factory
from app.routers import instruments, candles, signals, paper_trading, fyers, analysis
from app.routers import heatmap, trade_mode, settings, funds, limits, live_trades, scanner
from app import fyers_client
from app import auto_trade_engine

logger = logging.getLogger(__name__)


async def ensure_columns():
    """Add any missing columns to existing tables (handles schema migrations)."""
    from sqlalchemy import text

    migrations = [
        # instruments table
        ("instruments", "approx_price", "ALTER TABLE instruments ADD COLUMN approx_price FLOAT DEFAULT 0"),
        # signals table
        ("signals", "technical_score", "ALTER TABLE signals ADD COLUMN technical_score FLOAT"),
        ("signals", "fundamental_score", "ALTER TABLE signals ADD COLUMN fundamental_score FLOAT"),
        ("signals", "fundamental_data", "ALTER TABLE signals ADD COLUMN fundamental_data JSON"),
        ("signals", "sentiment_score", "ALTER TABLE signals ADD COLUMN sentiment_score FLOAT"),
        ("signals", "sentiment_data", "ALTER TABLE signals ADD COLUMN sentiment_data JSON"),
        ("signals", "fno_recommendation", "ALTER TABLE signals ADD COLUMN fno_recommendation JSON"),
        ("signals", "strategy", "ALTER TABLE signals ADD COLUMN strategy VARCHAR(50)"),
        ("signals", "sector", "ALTER TABLE signals ADD COLUMN sector VARCHAR(50)"),
        ("signals", "is_auto_generated", "ALTER TABLE signals ADD COLUMN is_auto_generated BOOLEAN DEFAULT false"),
        # paper_trades new columns
        ("paper_trades", "trade_ref", "ALTER TABLE paper_trades ADD COLUMN trade_ref VARCHAR(25) UNIQUE"),
        ("paper_trades", "display_symbol", "ALTER TABLE paper_trades ADD COLUMN display_symbol VARCHAR(30)"),
        ("paper_trades", "direction", "ALTER TABLE paper_trades ADD COLUMN direction VARCHAR(10)"),
        ("paper_trades", "sl_percent", "ALTER TABLE paper_trades ADD COLUMN sl_percent FLOAT"),
        ("paper_trades", "target_percent", "ALTER TABLE paper_trades ADD COLUMN target_percent FLOAT"),
        ("paper_trades", "strategy", "ALTER TABLE paper_trades ADD COLUMN strategy VARCHAR(30)"),
        ("paper_trades", "risk_reward", "ALTER TABLE paper_trades ADD COLUMN risk_reward FLOAT"),
        ("paper_trades", "signal_score", "ALTER TABLE paper_trades ADD COLUMN signal_score FLOAT"),
        ("paper_trades", "signal_strength", "ALTER TABLE paper_trades ADD COLUMN signal_strength VARCHAR(20)"),
        ("paper_trades", "highest_price", "ALTER TABLE paper_trades ADD COLUMN highest_price FLOAT"),
        ("paper_trades", "lowest_price", "ALTER TABLE paper_trades ADD COLUMN lowest_price FLOAT"),
        ("paper_trades", "max_runup", "ALTER TABLE paper_trades ADD COLUMN max_runup FLOAT"),
        ("paper_trades", "max_drawdown", "ALTER TABLE paper_trades ADD COLUMN max_drawdown FLOAT"),
        ("paper_trades", "signal_id", "ALTER TABLE paper_trades ADD COLUMN signal_id INTEGER"),
        # trading_settings new columns
        ("trading_settings", "auto_quantity_enabled", "ALTER TABLE trading_settings ADD COLUMN auto_quantity_enabled BOOLEAN DEFAULT true"),
        # auto-trade columns
        ("trading_settings", "auto_trade_enabled", "ALTER TABLE trading_settings ADD COLUMN auto_trade_enabled BOOLEAN DEFAULT false"),
        ("trading_settings", "scanner_running", "ALTER TABLE trading_settings ADD COLUMN scanner_running BOOLEAN DEFAULT false"),
        ("paper_trades", "exit_reason", "ALTER TABLE paper_trades ADD COLUMN exit_reason VARCHAR(50)"),
        ("paper_trades", "is_auto_trade", "ALTER TABLE paper_trades ADD COLUMN is_auto_trade BOOLEAN DEFAULT false"),
    ]

    async with async_session_factory() as db:
        for table, column, ddl in migrations:
            try:
                check = text("""
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = :table AND column_name = :column
                """)
                result = await db.execute(check, {"table": table, "column": column})
                if not result.scalar():
                    await db.execute(text(ddl))
                    await db.commit()
                    logger.info(f"Added missing column: {table}.{column}")
            except Exception as e:
                await db.rollback()
                logger.warning(f"Migration skip {table}.{column}: {e}")


async def seed_database():
    """Seed database with instruments and sample OHLCV data on first run."""
    from sqlalchemy import text
    from app.seed_data import get_all_instruments, generate_ohlcv_data, generate_sample_paper_trades

    async with async_session_factory() as db:
        # Check if already seeded
        result = await db.execute(text("SELECT COUNT(*) FROM instruments"))
        count = result.scalar()
        if count and count > 0:
            logger.info(f"Database already seeded ({count} instruments)")
            return

        logger.info("Seeding database with instruments and OHLCV data...")

        # Seed instruments
        all_instruments = get_all_instruments()
        for inst in all_instruments:
            await db.execute(text("""
                INSERT INTO instruments (symbol, exchange, segment, name, lot_size, tick_size,
                    expiry, strike, option_type, is_active, approx_price)
                VALUES (:symbol, :exchange, :segment, :name, :lot_size, :tick_size,
                    :expiry, :strike, :option_type, true, :approx_price)
                ON CONFLICT (symbol) DO NOTHING
            """), {
                "symbol": inst["symbol"],
                "exchange": inst.get("exchange", inst["symbol"].split(":")[0] if ":" in inst["symbol"] else "NSE"),
                "segment": inst["segment"],
                "name": inst["name"],
                "lot_size": inst.get("lot_size", 1),
                "tick_size": inst.get("tick_size", 0.05),
                "expiry": inst.get("expiry"),
                "strike": inst.get("strike"),
                "option_type": inst.get("option_type"),
                "approx_price": inst["price"],
            })

        # Seed OHLCV data for each instrument across timeframes
        timeframes = ["1D", "1W"]
        for inst in all_instruments:
            for tf in timeframes:
                days = 365 if tf == "1D" else 365 * 2
                candles_data = generate_ohlcv_data(inst["price"], days=days, timeframe=tf)
                for c in candles_data:
                    await db.execute(text("""
                        INSERT INTO ohlcv_candles (symbol, timestamp, timeframe, open, high, low, close, volume)
                        VALUES (:symbol, :timestamp, :timeframe, :open, :high, :low, :close, :volume)
                    """), {
                        "symbol": inst["symbol"],
                        "timestamp": c["timestamp"],
                        "timeframe": tf,
                        "open": c["open"],
                        "high": c["high"],
                        "low": c["low"],
                        "close": c["close"],
                        "volume": c["volume"],
                    })

        # Seed sample paper trades
        trades = generate_sample_paper_trades(all_instruments, count=30)
        for t in trades:
            await db.execute(text("""
                INSERT INTO paper_trades (symbol, instrument_type, timeframe, side, entry_price,
                    entry_time, exit_price, exit_time, quantity, stop_loss, target, status,
                    result, pnl_percent, pnl_amount, signal_confidence, signal_reasons,
                    indicators_snapshot, duration_minutes, exit_reason)
                VALUES (:symbol, :instrument_type, :timeframe, :side, :entry_price,
                    :entry_time, :exit_price, :exit_time, :quantity, :stop_loss, :target,
                    :status, :result, :pnl_percent, :pnl_amount, :signal_confidence,
                    :signal_reasons, :indicators_snapshot, :duration_minutes, :exit_reason)
            """), {
                "symbol": t["symbol"],
                "instrument_type": t["instrument_type"],
                "timeframe": t["timeframe"],
                "side": t["side"],
                "entry_price": t["entry_price"],
                "entry_time": t["entry_time"],
                "exit_price": t["exit_price"],
                "exit_time": t["exit_time"],
                "quantity": t["quantity"],
                "stop_loss": t["stop_loss"],
                "target": t["target"],
                "status": t["status"],
                "result": t["result"],
                "pnl_percent": t["pnl_percent"],
                "pnl_amount": t["pnl_amount"],
                "signal_confidence": t["signal_confidence"],
                "signal_reasons": json.dumps(t["signal_reasons"]),
                "indicators_snapshot": json.dumps(t.get("indicators_snapshot", {})),
                "duration_minutes": t["duration_minutes"],
                "exit_reason": t["exit_reason"],
            })

        await db.commit()
        logger.info(f"Seeded {len(all_instruments)} instruments with OHLCV data and {len(trades)} paper trades")


async def init_trading_settings():
    """Ensure trading_settings table has a default row."""
    from sqlalchemy import text
    async with async_session_factory() as db:
        try:
            result = await db.execute(text("SELECT COUNT(*) FROM trading_settings"))
            count = result.scalar()
            if not count or count == 0:
                await db.execute(text("""
                    INSERT INTO trading_settings (id, trade_mode, simulated_capital,
                        default_sl_percent, default_target_percent, default_quantity,
                        max_open_trades, allow_duplicate_symbol,
                        day_max_loss_paper, day_profit_target_paper,
                        day_max_loss_live, day_profit_target_live,
                        scan_frequency_minutes, top_movers_count, min_change_pct, min_volume)
                    VALUES (1, 'PAPER', 100000.0, 1.5, 3.0, 1, 5, false,
                            -1000.0, 3000.0, -2000.0, 5000.0, 5, 50, 0.5, 100000)
                """))
                await db.commit()
                logger.info("Trading settings initialized with defaults")
            else:
                logger.info("Trading settings already exist")
        except Exception as e:
            await db.rollback()
            logger.warning(f"Trading settings init: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - init DB and seed data."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    logger.info("Starting Stock Trading Platform...")
    await init_db()
    logger.info("Database tables created")
    await ensure_columns()
    await seed_database()
    await init_trading_settings()
    # Initialize Fyers client from saved token
    if fyers_client.init_fyers():
        logger.info("Fyers API client initialized with saved token")
    else:
        logger.info("Fyers API not authenticated - use /api/fyers/auth-url to connect")
    # Start auto-trade engine and monitor
    auto_trade_engine.start_engine()
    logger.info("Auto-trade engine and monitor started")
    logger.info("Startup complete - v3.0 NSE Auto Trading System")
    yield
    auto_trade_engine.stop_engine()
    # Release the persistent NSE httpx client cleanly.
    from app.heatmap_poller import heatmap_poller
    try:
        await heatmap_poller.close()
    except Exception as e:
        logger.warning(f"Heatmap poller close failed: {e}")
    logger.info("Shutting down...")


app = FastAPI(
    title="Stock Trading Platform",
    description="Intelligent Trading Analysis & Paper Trading System",
    version="2.0.0",
    lifespan=lifespan,
)

# Disable CORS. Do not remove this for full-stack development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Mount routers - existing
app.include_router(instruments.router)
app.include_router(candles.router)
app.include_router(signals.router)
app.include_router(paper_trading.router)
app.include_router(fyers.router)
app.include_router(analysis.router)
# Mount routers - v3.0 NSE Auto Trading
app.include_router(heatmap.router)
app.include_router(trade_mode.router)
app.include_router(settings.router)
app.include_router(funds.router)
app.include_router(limits.router)
app.include_router(live_trades.router)
app.include_router(scanner.router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/")
async def root():
    return {
        "name": "NSE Auto Trading System",
        "version": "3.0.0",
        "status": "running",
        "docs": "/docs",
        "endpoints": {
            "instruments": "/api/instruments",
            "candles": "/api/candles",
            "signals_analyze": "/api/signals/analyze",
            "signals_dashboard": "/api/signals/dashboard",
            "paper_trades": "/api/paper-trades",
            "paper_analytics": "/api/paper-trades/analytics",
            "fyers_status": "/api/fyers/status",
            "fyers_auth": "/api/fyers/auth-url",
            "comprehensive_analysis": "/api/analysis/comprehensive",
            "heatmap_live": "/api/v1/heatmap/live",
            "trade_mode": "/api/v1/trade-mode",
            "settings": "/api/v1/settings",
            "funds_combined": "/api/v1/funds/combined",
            "limits_status": "/api/v1/limits/status",
            "live_trades": "/api/v1/live/trades",
            "scanner_status": "/api/v1/scanner/status",
        }
    }
