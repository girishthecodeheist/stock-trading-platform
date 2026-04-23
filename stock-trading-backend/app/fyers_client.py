"""Fyers API v3 Client - OAuth, Historical Data, Live Quotes."""

import os
import json
import socket
import hashlib
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path

# --- Force IPv4 for all outbound HTTP traffic from this process ---
# Fyers' SEBI-compliant algo apps enforce IP whitelisting per request. If the
# OS happens to resolve their endpoints over IPv6, the outbound address does
# NOT match the whitelisted IPv4 the user registered on the Fyers dashboard,
# and the broker rejects every order with:
#   "Orders are only allowed from whitelisted IP addresses. This request was
#    received from IP: <v6-addr>."
# urllib3 (used transitively by fyers-apiv3 \u2192 requests) picks the address
# family via ``urllib3.util.connection.allowed_gai_family``. Overriding it to
# AF_INET guarantees every socket opened from this Python process uses IPv4.
# We also patch ``socket.getaddrinfo`` directly so anything talking raw
# sockets (e.g. websockets, httpx) behaves the same way.
if os.environ.get("FYERS_FORCE_IPV4", "1") not in ("0", "false", "False"):
    try:
        import urllib3.util.connection as _u3c
        _u3c.allowed_gai_family = lambda: socket.AF_INET
    except Exception:
        pass

    _original_getaddrinfo = socket.getaddrinfo

    def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return _original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = _ipv4_only_getaddrinfo

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


# Fyers order status codes (from Fyers API v3 docs). We mostly care about
# the three *terminal* ones — the rest mean "still in flight".
FYERS_STATUS_FILLED = 2
FYERS_STATUS_REJECTED = 5
FYERS_STATUS_CANCELLED = 1
FYERS_TERMINAL_STATUSES = {
    FYERS_STATUS_FILLED,
    FYERS_STATUS_REJECTED,
    FYERS_STATUS_CANCELLED,
}


def _normalise_status(code) -> str:
    mapping = {
        1: "CANCELLED",
        2: "FILLED",
        3: "PENDING",
        4: "PENDING",
        5: "REJECTED",
        6: "PENDING",
    }
    try:
        return mapping.get(int(code), "PENDING")
    except (TypeError, ValueError):
        return "PENDING"


def get_order_by_id(order_id: str) -> dict:
    """Look up a single Fyers order by ID.

    Returns a dict with keys {status, filled_qty, avg_price, remaining_qty,
    message, raw}. ``status`` is one of FILLED / PARTIAL / PENDING /
    REJECTED / CANCELLED / UNKNOWN. A PARTIAL fill (``tradedQty > 0`` but
    ``status != FILLED``) is reported so the caller can decide whether to
    cancel the remainder.
    """
    if not _fyers_model:
        return {
            "status": "UNKNOWN",
            "filled_qty": 0,
            "avg_price": 0.0,
            "remaining_qty": 0,
            "message": "Not authenticated",
            "raw": None,
        }
    try:
        resp = _fyers_model.orderbook({"id": order_id})
        if resp.get("s") != "ok":
            return {
                "status": "UNKNOWN",
                "filled_qty": 0,
                "avg_price": 0.0,
                "remaining_qty": 0,
                "message": resp.get("message") or "orderbook fetch failed",
                "raw": resp,
            }

        orders = resp.get("orderBook") or []
        if not orders and resp.get("id") == order_id:
            orders = [resp]
        match = None
        for o in orders:
            if str(o.get("id")) == str(order_id):
                match = o
                break
        if match is None:
            return {
                "status": "UNKNOWN",
                "filled_qty": 0,
                "avg_price": 0.0,
                "remaining_qty": 0,
                "message": "Order not found in orderbook",
                "raw": resp,
            }

        traded = int(match.get("tradedQty") or match.get("filledQty") or 0)
        req = int(match.get("qty") or 0)
        remaining = int(match.get("remainingQuantity") or max(req - traded, 0))
        avg = float(match.get("tradedPrice") or match.get("avgPrice") or 0.0)
        broker_status = _normalise_status(match.get("status"))
        # Upgrade PENDING → PARTIAL if some but not all qty has filled.
        if broker_status == "PENDING" and traded > 0 and traded < req:
            broker_status = "PARTIAL"
        if broker_status == "FILLED" and 0 < traded < req:
            broker_status = "PARTIAL"
        return {
            "status": broker_status,
            "filled_qty": traded,
            "avg_price": avg,
            "remaining_qty": remaining,
            "message": match.get("message") or "",
            "raw": match,
        }
    except Exception as e:
        logger.error(f"Fyers get_order_by_id error for {order_id}: {e}")
        return {
            "status": "UNKNOWN",
            "filled_qty": 0,
            "avg_price": 0.0,
            "remaining_qty": 0,
            "message": str(e),
            "raw": None,
        }


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


def get_news(symbol: str, limit: int = 15) -> list[dict]:
    """Fetch news for a symbol from Fyers API. Returns list of {title, source, date}.

    The Fyers API v3 SDK does not expose a native news endpoint as of this
    writing, so this function is intentionally structured as a stub: the
    architecture is in place for ``news_engine`` to prefer Fyers-sourced
    headlines when authenticated, and callers can fall back to other
    providers (yfinance / Google News RSS) when this returns ``[]``.

    Returns an empty list on any failure so downstream fallback chains can
    treat "no Fyers news" and "Fyers news unavailable" identically without
    special-casing.
    """
    if not _fyers_model:
        return []
    try:
        # Placeholder for a future direct HTTP call to a Fyers news endpoint.
        # When Fyers exposes one, replace this block with the actual request
        # (using the authenticated access token) and map results to
        # {"title", "source", "date"} dicts, capped at ``limit``.
        return []
    except Exception as e:
        logger.debug(f"Fyers news fetch failed for {symbol}: {e}")
        return []


# --- Async wrappers ---------------------------------------------------------
# The fyers_apiv3 SDK is synchronous. Every call (history/quotes/funds/...) does
# blocking network I/O. If we call these directly from inside an async FastAPI
# handler we block the entire event loop for the duration of the request,
# making every concurrent request wait. These wrappers offload the blocking
# SDK call to a worker thread via ``asyncio.to_thread`` so the event loop can
# keep serving other requests in parallel.


async def get_historical_data_async(
    symbol: str,
    timeframe: str = "1D",
    days_back: int = 365,
) -> list[dict]:
    """Async wrapper around ``get_historical_data``."""
    return await asyncio.to_thread(get_historical_data, symbol, timeframe, days_back)


async def get_live_prices_batch_async(symbols: list[str]) -> dict:
    """Async wrapper around ``get_live_prices_batch``."""
    return await asyncio.to_thread(get_live_prices_batch, symbols)


async def get_quotes_async(symbols: list[str]) -> dict:
    """Async wrapper around ``get_quotes``."""
    return await asyncio.to_thread(get_quotes, symbols)


async def get_funds_async() -> dict:
    """Async wrapper around ``get_funds``."""
    return await asyncio.to_thread(get_funds)


async def get_profile_async() -> dict:
    """Async wrapper around ``get_profile``."""
    return await asyncio.to_thread(get_profile)


async def get_positions_async() -> dict:
    """Async wrapper around ``get_positions``."""
    return await asyncio.to_thread(get_positions)


async def get_orders_async() -> dict:
    """Async wrapper around ``get_orders``."""
    return await asyncio.to_thread(get_orders)


async def get_order_by_id_async(order_id: str) -> dict:
    """Async wrapper around ``get_order_by_id``."""
    return await asyncio.to_thread(get_order_by_id, order_id)


async def reconcile_order_async(
    order_id: str,
    *,
    timeout_seconds: float = 15.0,
    poll_interval_seconds: float = 0.5,
) -> dict:
    """Poll Fyers until the order hits a terminal state or we time out.

    Returns the same shape as ``get_order_by_id``. Callers should inspect
    ``status`` (FILLED / PARTIAL / REJECTED / CANCELLED / PENDING /
    UNKNOWN) and ``filled_qty`` before committing trade rows — partial
    fills must never be treated as full fills.
    """
    if not order_id:
        return {
            "status": "UNKNOWN",
            "filled_qty": 0,
            "avg_price": 0.0,
            "remaining_qty": 0,
            "message": "Missing order id",
            "raw": None,
        }
    loop = asyncio.get_event_loop()
    deadline = loop.time() + max(timeout_seconds, 0.0)
    last: dict = {
        "status": "PENDING",
        "filled_qty": 0,
        "avg_price": 0.0,
        "remaining_qty": 0,
        "message": "",
        "raw": None,
    }
    terminal = {"FILLED", "REJECTED", "CANCELLED"}
    while True:
        info = await get_order_by_id_async(order_id)
        last = info
        if info.get("status") in terminal:
            return info
        # PARTIAL means some qty traded but order is still live — keep
        # polling; the remainder might fill before the deadline.
        if loop.time() >= deadline:
            return info
        await asyncio.sleep(max(poll_interval_seconds, 0.05))


async def get_market_depth_async(symbol: str) -> dict:
    """Async wrapper around ``get_market_depth``."""
    return await asyncio.to_thread(get_market_depth, symbol)


async def get_news_async(symbol: str, limit: int = 15) -> list[dict]:
    """Async wrapper around ``get_news``."""
    return await asyncio.to_thread(get_news, symbol, limit)


async def place_order_async(
    symbol: str,
    side: int,
    qty: int,
    order_type: int = 2,
    product_type: str = "INTRADAY",
    price: float = 0,
    stop_price: float = 0,
) -> dict:
    """Async wrapper around ``place_order``."""
    return await asyncio.to_thread(
        place_order, symbol, side, qty, order_type, product_type, price, stop_price
    )
