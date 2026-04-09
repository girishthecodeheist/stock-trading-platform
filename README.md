# TradeIQ - Intelligent Stock Trading & Paper Trading Platform

A production-ready trading analysis and paper trading simulator built with **FastAPI** (Python) + **Angular 17** + **PostgreSQL** + **Fyers API v3** for real-time live market data.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Step 1: PostgreSQL Database Setup](#step-1-postgresql-database-setup)
- [Step 2: Backend Setup](#step-2-backend-setup)
- [Step 3: Frontend Setup](#step-3-frontend-setup)
- [Step 4: Fyers API Setup (Live Market Data)](#step-4-fyers-api-setup-live-market-data)
- [Project Structure](#project-structure)
- [API Endpoints Reference](#api-endpoints-reference)
- [Features](#features)
- [Technical Indicators](#technical-indicators)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

Make sure the following are installed on your system:

| Tool        | Version   | Installation                                                    |
|-------------|-----------|-----------------------------------------------------------------|
| Python      | 3.12+     | [python.org](https://python.org) or `brew install python@3.12`    |
| Poetry      | 1.7+      | `curl -sSL https://install.python-poetry.org \| python3 -`       |
| Node.js     | 18+       | [nodejs.org](https://nodejs.org) or `brew install node`           |
| Angular CLI | 17.3+     | `npm install -g @angular/cli@17`                                |
| PostgreSQL  | 14+       | `brew install postgresql@16` (macOS) or `sudo apt install postgresql` (Linux) |
| pgAdmin     | (optional)| [pgadmin.org](https://www.pgadmin.org/download/)                |

---

## Step 1: PostgreSQL Database Setup

### Option A: macOS (Homebrew)

```bash
# 1. Install and start PostgreSQL (skip if already installed)
brew install postgresql@16
brew services start postgresql@16

# 2. Create user and database
psql postgres -c "CREATE ROLE \"user\" WITH LOGIN PASSWORD 'password';"
psql postgres -c "CREATE DATABASE trading_platform OWNER \"user\";"
psql -d trading_platform -c "GRANT ALL ON SCHEMA public TO \"user\"; ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO \"user\"; ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO \"user\";"
```

> **Note for macOS**: If you installed via **Postgres.app**, replace `psql` with:
> `/Applications/Postgres.app/Contents/Versions/latest/bin/psql`

### Option B: Linux (Ubuntu/Debian)

```bash
# 1. Install and start PostgreSQL (skip if already installed)
sudo apt install postgresql postgresql-contrib
sudo systemctl start postgresql

# 2. Create user and database
sudo -u postgres psql -c "CREATE ROLE \"user\" WITH LOGIN PASSWORD 'password';"
sudo -u postgres psql -c "CREATE DATABASE trading_platform OWNER \"user\";"
sudo -u postgres psql -d trading_platform -c "GRANT ALL ON SCHEMA public TO \"user\"; ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO \"user\"; ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO \"user\";"
```

### Option C: Using psql Interactive Shell

```bash
# Connect to PostgreSQL (macOS: psql postgres | Linux: sudo -u postgres psql)
psql postgres
```

Then run these SQL commands:

```sql
-- ============================================
-- DATABASE CREATION SCRIPT FOR TRADEIQ
-- ============================================

-- 1. Create the database user
CREATE ROLE "user" WITH LOGIN PASSWORD 'password';

-- 2. Create the database
CREATE DATABASE trading_platform OWNER "user";

-- 3. Grant privileges
GRANT ALL PRIVILEGES ON DATABASE trading_platform TO "user";

-- 4. Connect to the database
\c trading_platform

-- 5. Grant schema privileges
GRANT ALL ON SCHEMA public TO "user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO "user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO "user";

-- 6. Exit
\q
```

### Option D: Using pgAdmin

1. Open pgAdmin and connect to your PostgreSQL server
2. Right-click **Login/Group Roles** → Create → Login/Group Role
   - **General tab**: Name = `user`
   - **Definition tab**: Password = `password`
   - **Privileges tab**: Enable "Can login?"
3. Right-click **Databases** → Create → Database
   - **General tab**: Database = `trading_platform`, Owner = `user`
4. Click **Save**

### Verify Database Connection

```bash
psql -h localhost -U user -d trading_platform -c "SELECT 1 AS connected;"
# Enter password: password
# Should show: connected = 1
```

> **Note**: Tables are auto-created by the backend on first startup. You do NOT need to manually create any tables. The application also auto-seeds 47 instruments with 365 days of OHLCV data and 30 sample paper trades.

---

## Step 2: Backend Setup

```bash
# Navigate to backend directory
cd stock-trading-backend

# Install Python dependencies using Poetry
poetry install

# (Optional) Create .env file to customize database connection
cat > .env << 'EOF'
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/trading_platform
FYERS_APP_ID=1DD7D7XSFX-100
FYERS_SECRET_KEY=298GVVWZUT
FYERS_REDIRECT_URI=http://localhost:8000/api/fyers/callback
EOF

# Start the backend server
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### What happens on first startup:
1. Connects to PostgreSQL
2. Creates all tables automatically (`instruments`, `ohlcv_candles`, `signals`, `paper_trades`)
3. Creates a unique index on `(symbol, timestamp, timeframe)` for candle upsert support
4. Seeds 47 instruments (30 Equity, 4 Index, 5 Futures, 8 Options)
5. Seeds 365 days of OHLCV data per instrument per timeframe
6. Seeds 30 sample paper trades (mix of open and closed)
7. Attempts to initialize Fyers API from saved token (if available)

### Verify Backend is Running

```bash
# Health check
curl http://localhost:8000/healthz
# Expected: {"status":"ok"}

# List instruments
curl http://localhost:8000/api/instruments | python3 -m json.tool | head -20

# Check Fyers status
curl http://localhost:8000/api/fyers/status
# Expected: {"authenticated":false,"app_id":"1DD7D7XSFX-100","redirect_uri":"http://localhost:8000/api/fyers/callback"}
```

### Interactive API Docs

Open http://localhost:8000/docs in your browser to see the full Swagger UI with all endpoints.

---

## Step 3: Frontend Setup

```bash
# Navigate to frontend directory
cd stock-trading-frontend

# Install Node.js dependencies
npm install

# Start the Angular dev server
npx ng serve --port 4200 --host 0.0.0.0
```

### Open the Application

Open http://localhost:4200 in your browser.

**Pages:**
| Page            | URL                            | Description                              |
|-----------------|--------------------------------|------------------------------------------|
| Dashboard       | `/dashboard`                   | Signal scanner with all 47 instruments   |
| Chart           | `/chart?symbol=NSE:RELIANCE-EQ`| TradingView chart + signal analysis      |
| Paper Trading   | `/paper-trading`               | Active & closed trades dashboard         |
| Analytics       | `/analytics`                   | Performance metrics & win/loss stats     |

---

## Step 4: Fyers API Setup (Live Market Data)

The app works out of the box with seeded data. To enable **real-time live market data** from Fyers:

### 4.1: Create a Fyers API App

1. Go to https://myapi.fyers.in/dashboard
2. Login with your Fyers account
3. Create a new app (or use existing one)
4. Set the **Redirect URL** to: `http://localhost:8000/api/fyers/callback`
5. Note your **App ID** and **Secret ID**

### 4.2: Update Credentials (if different from defaults)

Edit `stock-trading-backend/.env`:

```env
FYERS_APP_ID=YOUR_APP_ID_HERE
FYERS_SECRET_KEY=YOUR_SECRET_KEY_HERE
FYERS_REDIRECT_URI=http://localhost:8000/api/fyers/callback
```

### 4.3: Authenticate with Fyers

1. Start both backend and frontend servers
2. In the sidebar, click **"Connect Fyers"** (shows red dot when disconnected)
3. You'll be redirected to the Fyers login page
4. Enter your Fyers credentials (TOTP if enabled)
5. After successful login, you'll be redirected back to the app
6. The sidebar will show a **green pulsing dot** + **"Fyers Live"**
7. The access token is saved to `.fyers_token.json` and auto-loaded on next startup

### What Changes with Fyers Connected:

| Feature              | Without Fyers              | With Fyers Connected         |
|----------------------|----------------------------|------------------------------|
| Data Source Badge    | "CACHED"                   | "FYERS LIVE"                 |
| Dashboard Title      | "Signal Scanner"           | "Signal Scanner" + LIVE badge|
| Price Updates        | Static (seeded data)       | Real-time every 3-5 seconds  |
| Chart Data           | From database              | Fresh from Fyers API         |
| Signals              | Based on seeded data       | Based on live market data    |
| Sidebar Status       | Red dot + "Connect Fyers"  | Green dot + "Fyers Live"     |

> **Note**: Fyers access tokens expire daily. You'll need to re-authenticate each trading day by clicking "Connect Fyers" again.

---

## Project Structure

```
stock-trading-platform/
├── README.md                          # This file
├── stock-trading-backend/
│   ├── pyproject.toml                 # Python dependencies (Poetry)
│   ├── poetry.lock                    # Locked dependency versions
│   ├── .env                           # Environment variables
│   ├── .fyers_token.json              # Saved Fyers access token (auto-generated)
│   └── app/
│       ├── main.py                    # FastAPI app entry point + startup/seed logic
│       ├── database.py                # PostgreSQL async engine + session factory
│       ├── models.py                  # SQLAlchemy models (Instrument, OHLCVCandle, Signal, PaperTrade)
│       ├── seed_data.py               # Data seeder (47 instruments + OHLCV + sample trades)
│       ├── indicator_engine.py        # Technical indicators (RSI, MACD, MA, BB, ATR, Stochastic, S&R)
│       ├── signal_engine.py           # Explainable signal generator (BUY/SELL/HOLD with reasons)
│       ├── fyers_client.py            # Fyers API v3 client (OAuth, historical data, live quotes)
│       └── routers/
│           ├── instruments.py         # GET /api/instruments - list & search instruments
│           ├── candles.py             # GET /api/candles - OHLCV data (Fyers live or database)
│           ├── signals.py             # GET /api/signals/* - signal analysis & dashboard scan
│           ├── paper_trading.py       # /api/paper-trades - CRUD + analytics for paper trades
│           └── fyers.py               # /api/fyers/* - OAuth flow, live quotes, SSE streaming
│
└── stock-trading-frontend/
    ├── package.json                   # Angular 17 dependencies
    ├── angular.json                   # Angular workspace configuration
    └── src/app/
        ├── app.component.ts           # Root component with sidebar + Fyers status
        ├── app.routes.ts              # Route definitions
        ├── services/
        │   └── api.service.ts         # HTTP client for all backend API calls
        └── pages/
            ├── dashboard/             # Signal scanner grid (search, filter, signal cards)
            ├── chart/                 # TradingView chart + signal card + indicators panel
            ├── paper-trading/         # Active/closed trades + summary stats
            └── analytics/             # Performance analytics + win/loss distribution
```

---

## API Endpoints Reference

### Instruments
| Method | Endpoint                  | Description                        | Parameters                    |
|--------|---------------------------|------------------------------------|-------------------------------|
| GET    | `/api/instruments`        | List instruments with filters      | `?segment=EQUITY&search=RELIANCE` |
| GET    | `/api/instruments/segments` | List available segments          | -                             |

### Candles (OHLCV)
| Method | Endpoint           | Description                           | Parameters                          |
|--------|--------------------|---------------------------------------|-------------------------------------|
| GET    | `/api/candles`     | Get OHLCV candles (Fyers or database) | `?symbol=NSE:RELIANCE-EQ&timeframe=1D&limit=300&live=true` |

### Signals
| Method | Endpoint                | Description                        | Parameters                              |
|--------|-------------------------|------------------------------------|-----------------------------------------|
| GET    | `/api/signals/analyze`  | Analyze a symbol (indicators + signal) | `?symbol=NSE:RELIANCE-EQ&timeframe=1D` |
| GET    | `/api/signals/dashboard`| Scan all instruments for dashboard | `?segment=EQUITY&limit=50`             |
| GET    | `/api/signals/history`  | List historical signals            | `?symbol=...&type=BUY&status=OPEN`     |

### Paper Trading
| Method | Endpoint                       | Description              | Body/Parameters             |
|--------|--------------------------------|--------------------------|-----------------------------|
| GET    | `/api/paper-trades`            | List paper trades        | `?status=OPEN&symbol=...`  |
| POST   | `/api/paper-trades`            | Create a new paper trade | `{symbol, entry_price, side, ...}` |
| POST   | `/api/paper-trades/{id}/close` | Close an open trade      | `{exit_price, exit_reason}` |
| GET    | `/api/paper-trades/analytics`  | Performance analytics    | -                           |

### Fyers API
| Method | Endpoint                  | Description                        | Parameters                    |
|--------|---------------------------|------------------------------------|-------------------------------|
| GET    | `/api/fyers/status`       | Check Fyers authentication status  | -                             |
| GET    | `/api/fyers/auth-url`     | Get OAuth login URL                | -                             |
| GET    | `/api/fyers/callback`     | OAuth callback (auto-handled)      | `?auth_code=...`              |
| GET    | `/api/fyers/quotes`       | Live quotes for symbols            | `?symbols=NSE:RELIANCE-EQ`    |
| GET    | `/api/fyers/history`      | Fetch & store historical data      | `?symbol=...&timeframe=1D&days=365` |
| GET    | `/api/fyers/sync-all`     | Sync all instruments from Fyers    | `?timeframe=1D&days=365`      |
| GET    | `/api/fyers/live-prices`  | Get live prices for all/selected   | `?symbols=NSE:RELIANCE-EQ,...`|
| GET    | `/api/fyers/stream`       | SSE stream of live prices          | `?interval=3&symbols=...`     |

### System
| Method | Endpoint    | Description    |
|--------|-------------|----------------|
| GET    | `/healthz`  | Health check   |
| GET    | `/`         | API info       |
| GET    | `/docs`     | Swagger UI     |

---

## Features

### Multi-Asset Support
- **Equity** (30 stocks): RELIANCE, TCS, INFY, HDFC, etc.
- **Index** (4): NIFTY IT, BANK NIFTY, etc.
- **Futures** (5): RELIANCE FUT, NIFTY FUT, etc.
- **Options** (8): NIFTY CE/PE, RELIANCE CE/PE, BANKNIFTY CE/PE

### Timeframe Support
- 1 Minute, 5 Minutes, 15 Minutes
- 1 Day, 1 Week, 1 Year

### Explainable Signals
Every signal includes reasoning:
```json
{
  "signal": "STRONG BUY",
  "confidence": 82,
  "reasons": [
    "RSI below 30 (oversold)",
    "MACD bullish crossover - histogram positive",
    "Strong uptrend: Price > SMA20 > SMA50 > SMA200",
    "Price near support level S1 - potential bounce",
    "Price approaching resistance R1"
  ]
}
```

### Paper Trading Simulator
- Auto-simulates trades based on signal strength
- Tracks entry/exit price, stop loss, targets
- Calculates P&L, win rate, average profit/loss
- Manual close with reason tracking
- Performance analytics dashboard

### Live Market Data (Fyers)
- Real-time price updates via Server-Sent Events (SSE)
- Auto-caches fetched data in PostgreSQL
- Graceful fallback to database when disconnected
- Data source indicator (FYERS LIVE / CACHED)

---

## Technical Indicators

| Indicator             | Parameters       | Description                           |
|-----------------------|------------------|---------------------------------------|
| RSI                   | Period: 14       | Relative Strength Index               |
| MACD                  | 12, 26, 9        | Moving Average Convergence Divergence |
| SMA                   | 20, 50, 200      | Simple Moving Averages                |
| EMA                   | 9, 20, 50, 200   | Exponential Moving Averages           |
| Bollinger Bands       | 20, 2σ           | Upper, Middle, Lower bands            |
| ATR                   | Period: 14       | Average True Range                    |
| Stochastic Oscillator | 14, 3, 3         | %K and %D lines                       |
| Volume Trend          | SMA 20           | HIGH / NORMAL / LOW classification    |
| Support & Resistance  | Pivot Points     | S1, S2, R1, R2 levels                 |

---

## Configuration

### Environment Variables (`stock-trading-backend/.env`)

| Variable              | Default                                                              | Description                |
|-----------------------|----------------------------------------------------------------------|----------------------------|
| `DATABASE_URL`        | `postgresql+asyncpg://user:password@localhost:5432/trading_platform`  | PostgreSQL connection URL  |
| `FYERS_APP_ID`        | `1DD7D7XSFX-100`                                                    | Fyers API App ID           |
| `FYERS_SECRET_KEY`    | `298GVVWZUT`                                                         | Fyers API Secret Key       |
| `FYERS_REDIRECT_URI`  | `http://localhost:8000/api/fyers/callback`                           | OAuth redirect URL         |

### Database Tables (Auto-Created)

```
instruments       - 47 seeded instruments (stocks, indices, futures, options)
ohlcv_candles     - OHLCV price data with timeframe support
signals           - Generated trading signals with reasoning
paper_trades      - Paper trading records with P&L tracking
```

---

## Troubleshooting

### Database connection refused
```bash
# Check PostgreSQL is running
sudo systemctl status postgresql

# Start PostgreSQL if stopped
sudo systemctl start postgresql

# Verify the database exists
sudo -u postgres psql -l | grep trading_platform
```

### Port already in use
```bash
# Kill process on port 8000 (backend)
lsof -ti:8000 | xargs kill -9

# Kill process on port 4200 (frontend)
lsof -ti:4200 | xargs kill -9
```

### Poetry not found
```bash
# Install Poetry
curl -sSL https://install.python-poetry.org | python3 -

# Add to PATH
export PATH="$HOME/.local/bin:$PATH"
```

### Angular CLI not found
```bash
# Install globally
npm install -g @angular/cli@17

# Or use npx (no global install needed)
npx ng serve --port 4200
```

### Fyers token expired
Fyers access tokens expire at the end of each trading day. Simply click **"Connect Fyers"** in the sidebar to re-authenticate.

### Tables not being created
The app creates tables automatically on startup. If tables are missing:
```bash
# Check backend logs for errors
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level debug
```

### Reset database (fresh start)
```bash
sudo -u postgres psql -c "DROP DATABASE IF EXISTS trading_platform;"
sudo -u postgres psql -c "CREATE DATABASE trading_platform OWNER \"user\";"
sudo -u postgres psql -d trading_platform -c "GRANT ALL ON SCHEMA public TO \"user\";"
# Restart backend - tables will be re-created and data re-seeded
```
