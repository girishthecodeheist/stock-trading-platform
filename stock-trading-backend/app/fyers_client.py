"""Fyers API v3 Client - OAuth, Historical Data, Live Quotes."""

import os
import json
import hashlib
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path

from fyers_apiv3 import fyersModel

logger = logging.getLogger(__name__)

# Fyers credentials
FYERS_APP_ID = os.environ.get("FYERS_APP_ID", "1DD7D7XSFX-100")
FYERS_SECRET_KEY = os.environ.get("FYERS_SECRET_KEY", "298GVVWZUT")
FYERS_REDIRECT_URI = os.environ.get("FYERS_REDIRECT_URI", "http://localhost:8000/api/fyers/callback")

# Token persistence file
TOKEN_FILE = Path(__file__).parent.parent / ".fyers_token.json"

# Global state
_access_token: Optional[str] = None
_fyers_model: Optional[fyersModel.FyersModel] = None


def _get_app_id_hash() -> str:
    """Generate SHA256 hash of app_id:secret_key for Fyers API."""
    raw = f"{FYERS_APP_ID}:{FYERS_SECRET_KEY}"
    return hashlib.sha256(raw.encode()).hexdigest()


def get_auth_url() -> str:
    """Generate the Fyers OAuth authorization URL for user to login."""
    session = fyersModel.SessionModel(
        client_id=FYERS_APP_ID,
        secret_key=FYERS_SECRET_KEY,
        redirect_uri=FYERS_REDIRECT_URI,
        response_type="code",
        state="trading_platform",
    )
    return session.generate_authcode()


def generate_access_token(auth_code: str) -> dict:
    """Exchange auth code for access token."""
    global _access_token, _fyers_model

    session = fyersModel.SessionModel(
        client_id=FYERS_APP_ID,
        secret_key=FYERS_SECRET_KEY,
        redirect_uri=FYERS_REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code",
    )
    session.set_token(auth_code)
    response = session.generate_token()

    if response.get("s") == "ok" and response.get("access_token"):
        _access_token = response["access_token"]
        _fyers_model = fyersModel.FyersModel(
            client_id=FYERS_APP_ID,
            token=_access_token,
            is_async=False,
            log_path="",
        )
        # Save token to file for persistence
        _save_token(_access_token)
        logger.info("Fyers access token generated and saved successfully")
        return {"status": "ok", "message": "Token generated successfully"}
    else:
        logger.error(f"Fyers token generation failed: {response}")
        return {"status": "error", "message": response.get("message", "Token generation failed"), "details": response}


def _save_token(token: str):
    """Save access token to file."""
    try:
        data = {
            "access_token": token,
            "timestamp": datetime.utcnow().isoformat(),
            "app_id": FYERS_APP_ID,
        }
        TOKEN_FILE.write_text(json.dumps(data))
    except Exception as e:
        logger.error(f"Failed to save token: {e}")


def _load_token() -> Optional[str]:
    """Load access token from file."""
    try:
        if TOKEN_FILE.exists():
            data = json.loads(TOKEN_FILE.read_text())
            # Check if token is from today (Fyers tokens expire daily)
            ts = datetime.fromisoformat(data["timestamp"])
            if ts.date() == datetime.utcnow().date():
                return data["access_token"]
            else:
                logger.info("Saved Fyers token expired (from different day)")
    except Exception as e:
        logger.error(f"Failed to load token: {e}")
    return None


def init_fyers():
    """Initialize Fyers client from saved token if available."""
    global _access_token, _fyers_model
    token = _load_token()
    if token:
        _access_token = token
        _fyers_model = fyersModel.FyersModel(
            client_id=FYERS_APP_ID,
            token=_access_token,
            is_async=False,
            log_path="",
        )
        logger.info("Fyers client initialized from saved token")
        return True
    return False


def is_authenticated() -> bool:
    """Check if we have a valid Fyers session."""
    return _fyers_model is not None


def get_fyers_model() -> Optional[fyersModel.FyersModel]:
    """Get the initialized Fyers model."""
    return _fyers_model


def get_profile() -> dict:
    """Get Fyers user profile to verify token."""
    if not _fyers_model:
        return {"status": "error", "message": "Not authenticated"}
    try:
        return _fyers_model.get_profile()
    except Exception as e:
        return {"status": "error", "message": str(e)}


def get_quotes(symbols: list[str]) -> dict:
    """Get live quotes for symbols. Symbols should be in Fyers format e.g. NSE:RELIANCE-EQ."""
    if not _fyers_model:
        return {"status": "error", "message": "Not authenticated"}
    try:
        data = {"symbols": ",".join(symbols)}
        return _fyers_model.quotes(data)
    except Exception as e:
        logger.error(f"Fyers quotes error: {e}")
        return {"status": "error", "message": str(e)}


def place_order(
    symbol: str,
    side: int,
    qty: int,
    order_type: int = 2,
    product_type: str = "INTRADAY",
    price: float = 0,
    stop_price: float = 0,
) -> dict:
    """Place a real order on Fyers.

    side: 1=BUY, -1=SELL
    order_type: 1=LIMIT, 2=MARKET, 3=SL-MARKET, 4=SL-LIMIT
    product_type: INTRADAY, CNC, MARGIN
    """
    if not _fyers_model:
        return {"s": "error", "status": "error", "message": "Not authenticated"}
    data = {
        "symbol": symbol,
        "qty": qty,
        "type": order_type,
        "side": side,
        "productType": product_type,
        "limitPrice": price,
        "stopPrice": stop_price,
        "validity": "DAY",
        "disclosedQty": 0,
        "offlineOrder": False,
    }
    try:
        response = _fyers_model.place_order(data)
        logger.info(f"Fyers place_order request={data} response={response}")
        return response
    except Exception as e:
        logger.error(f"Fyers place_order error: {e}")
        return {"s": "error", "status": "error", "message": str(e)}


def get_positions() -> dict:
    """Get current Fyers positions."""
    if not _fyers_model:
        return {"s": "error", "status": "error", "message": "Not authenticated"}
    try:
        return _fyers_model.positions()
    except Exception as e:
        logger.error(f"Fyers positions error: {e}")
        return {"s": "error", "status": "error", "message": str(e)}


def get_orders() -> dict:
    """Get the Fyers orderbook."""
    if not _fyers_model:
        return {"s": "error", "status": "error", "message": "Not authenticated"}
    try:
        return _fyers_model.orderbook()
    except Exception as e:
        logger.error(f"Fyers orderbook error: {e}")
        return {"s": "error", "status": "error", "message": str(e)}


def get_funds() -> dict:
    """Get Fyers account fund limits / wallet balance."""
    if not _fyers_model:
        return {"s": "error", "status": "error", "message": "Not authenticated"}
    try:
        return _fyers_model.funds()
    except Exception as e:
        logger.error(f"Fyers funds error: {e}")
        return {"s": "error", "status": "error", "message": str(e)}


def get_market_depth(symbol: str) -> dict:
    """Get market depth for a symbol."""
    if not _fyers_model:
        return {"status": "error", "message": "Not authenticated"}
    try:
        data = {"symbol": symbol, "ohlcv_flag": "1"}
        return _fyers_model.depth(data)
    except Exception as e:
        logger.error(f"Fyers depth error: {e}")
        return {"status": "error", "message": str(e)}


# Fyers timeframe mapping
FYERS_RESOLUTION_MAP = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "1h": "60",
    "1D": "1D",
    "1W": "1W",
    "1Y": "1M",  # Monthly data for yearly view
}


def get_historical_data(
    symbol: str,
    timeframe: str = "1D",
    days_back: int = 365,
) -> list[dict]:
    """Fetch historical OHLCV data from Fyers API.
    
    Returns list of candle dicts: {timestamp, open, high, low, close, volume}
    """
    if not _fyers_model:
        return []

    resolution = FYERS_RESOLUTION_MAP.get(timeframe, "D")
    
    # Calculate date range
    end_date = datetime.now()
    
    # Fyers has limits on how far back you can go for intraday
    if timeframe in ("1m", "5m", "15m"):
        start_date = end_date - timedelta(days=min(days_back, 30))  # Max 30 days for intraday
    elif timeframe == "1h":
        start_date = end_date - timedelta(days=min(days_back, 365))
    else:
        start_date = end_date - timedelta(days=days_back)

    try:
        data = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",  # epoch
            "range_from": start_date.strftime("%Y-%m-%d"),
            "range_to": end_date.strftime("%Y-%m-%d"),
            "cont_flag": "1",  # continuous data
        }

        response = _fyers_model.history(data)

        if response.get("s") != "ok":
            logger.error(f"Fyers history error for {symbol}: {response}")
            return []

        candles = []
        for c in response.get("candles", []):
            # Fyers returns: [epoch, open, high, low, close, volume]
            candles.append({
                "timestamp": datetime.fromtimestamp(c[0]).isoformat(),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": int(c[5]),
            })

        logger.info(f"Fetched {len(candles)} candles for {symbol} ({timeframe})")
        return candles

    except Exception as e:
        logger.error(f"Fyers history fetch error for {symbol}: {e}")
        return []


def get_live_prices_batch(symbols: list[str]) -> dict:
    """Get live prices for multiple symbols. Returns dict of symbol -> price data."""
    if not _fyers_model:
        return {}
    
    try:
        data = {"symbols": ",".join(symbols)}
        response = _fyers_model.quotes(data)
        
        if response.get("s") != "ok":
            return {}
        
        prices = {}
        for q in response.get("d", []):
            v = q.get("v", {})
            sym = v.get("symbol", q.get("n", ""))
            if sym:
                prices[sym] = {
                    "ltp": v.get("lp", 0),
                    "open": v.get("open_price", 0),
                    "high": v.get("high_price", 0),
                    "low": v.get("low_price", 0),
                    "close": v.get("prev_close_price", 0),
                    "volume": v.get("volume", 0),
                    "change": v.get("ch", 0),
                    "change_pct": v.get("chp", 0),
                    "bid": v.get("bid", 0),
                    "ask": v.get("ask", 0),
                    "timestamp": datetime.now().isoformat(),
                }
        return prices
    except Exception as e:
        logger.error(f"Fyers batch quotes error: {e}")
        return {}
