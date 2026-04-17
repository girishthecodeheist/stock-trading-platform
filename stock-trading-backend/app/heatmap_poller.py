"""NSE Heatmap Poller Service - fetches live market data from NSE."""

import logging
import asyncio
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

NSE_SECTOR_ENDPOINTS = {
    "NIFTY50": "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050",
    "BANKNIFTY": "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20BANK",
    "IT": "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20IT",
    "AUTO": "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20AUTO",
    "PHARMA": "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20PHARMA",
    "FMCG": "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20FMCG",
    "METAL": "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20METAL",
    "REALTY": "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20REALTY",
    "ENERGY": "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20ENERGY",
    "MIDCAP": "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20MIDCAP%20100",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/market-data/live-equity-market",
}


class HeatmapPollerService:
    def __init__(self):
        self.session_cookies: dict = {}
        self.active_movers: dict = {}
        self.sectors_data: dict = {}
        self.last_poll_time: Optional[datetime] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # Reuse a single httpx client across polls. NSE's API is sensitive to
        # creating many TCP connections and keep-alive dramatically reduces the
        # connection/TLS handshake overhead per poll (10 sectors * N polls).
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        """Return a persistent httpx client, creating one if needed."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=HEADERS, follow_redirects=True, timeout=15
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying httpx client (use on shutdown)."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def init_nse_session(self):
        """Get session cookies from NSE website."""
        try:
            client = self._get_client()
            resp = await client.get("https://www.nseindia.com")
            self.session_cookies = dict(resp.cookies)
            await client.get(
                "https://www.nseindia.com/market-data/live-equity-market",
                cookies=self.session_cookies, timeout=10
            )
            logger.info("NSE session initialized with cookies")
        except Exception as e:
            logger.warning(f"NSE session init failed: {e}")

    async def fetch_sector(self, sector_name: str, url: str) -> list:
        """Fetch one sector's stock list from NSE API."""
        try:
            client = self._get_client()
            resp = await client.get(url, cookies=self.session_cookies)
            if resp.status_code != 200:
                logger.warning(f"NSE API returned {resp.status_code} for {sector_name}")
                return []
            data = resp.json()
            stocks = data.get("data", [])
            result = []
            for s in stocks:
                if s.get("symbol", "").startswith("NIFTY"):
                    continue  # Skip index rows
                symbol = f"NSE:{s['symbol']}-EQ"
                result.append({
                    "symbol": symbol,
                    "display_symbol": s["symbol"],
                    "sector": sector_name,
                    "ltp": float(s.get("lastPrice", "0").replace(",", "") if isinstance(s.get("lastPrice"), str) else s.get("lastPrice", 0)),
                    "change_pct": float(s.get("pChange", 0)),
                    "volume": int(s.get("totalTradedVolume", 0)),
                    "open_price": float(s.get("open", "0").replace(",", "") if isinstance(s.get("open"), str) else s.get("open", 0)),
                    "high_price": float(s.get("dayHigh", "0").replace(",", "") if isinstance(s.get("dayHigh"), str) else s.get("dayHigh", 0)),
                    "low_price": float(s.get("dayLow", "0").replace(",", "") if isinstance(s.get("dayLow"), str) else s.get("dayLow", 0)),
                    "prev_close": float(s.get("previousClose", "0").replace(",", "") if isinstance(s.get("previousClose"), str) else s.get("previousClose", 0)),
                    "fetched_at": datetime.utcnow().isoformat(),
                })
            return result
        except Exception as e:
            logger.warning(f"Heatmap fetch failed for {sector_name}: {e}")
            return []

    async def poll_heatmap(self) -> dict:
        """Main polling function - fetches all sectors concurrently."""
        await self.init_nse_session()

        tasks = [
            self.fetch_sector(name, url)
            for name, url in NSE_SECTOR_ENDPOINTS.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_stocks = []
        for r in results:
            if isinstance(r, list):
                all_stocks.extend(r)

        # Deduplicate by symbol
        seen = {}
        for s in all_stocks:
            sym = s["symbol"]
            if sym not in seen or abs(s["change_pct"]) > abs(seen[sym]["change_pct"]):
                seen[sym] = s

        self.active_movers = seen
        self.last_poll_time = datetime.utcnow()

        # Build sector summary
        self._build_sector_summary()

        logger.info(f"Heatmap polled: {len(seen)} unique stocks from {len(NSE_SECTOR_ENDPOINTS)} sectors")
        return {
            "total_stocks": len(seen),
            "sectors": len(self.sectors_data),
            "poll_time": self.last_poll_time.isoformat() if self.last_poll_time else None,
        }

    def _build_sector_summary(self):
        """Build per-sector summary data."""
        by_sector = {}
        for s in self.active_movers.values():
            sector = s["sector"]
            if sector not in by_sector:
                by_sector[sector] = []
            by_sector[sector].append(s)

        self.sectors_data = {}
        for sector, stocks in by_sector.items():
            sorted_stocks = sorted(stocks, key=lambda x: x["change_pct"], reverse=True)
            avg_change = sum(s["change_pct"] for s in sorted_stocks) / len(sorted_stocks) if sorted_stocks else 0
            self.sectors_data[sector] = {
                "name": sector,
                "top_gainer": sorted_stocks[0] if sorted_stocks else None,
                "top_loser": sorted_stocks[-1] if sorted_stocks else None,
                "avg_change": round(avg_change, 2),
                "sector_bias": "BULLISH" if avg_change > 0 else "BEARISH",
                "stock_count": len(stocks),
            }

    def get_top_movers(self, n: int = 50, min_volume: int = 100000,
                       min_price: float = 20.0, min_change_pct: float = 0.5) -> list:
        """Return top N stocks by absolute change %."""
        stocks = list(self.active_movers.values())
        stocks = [
            s for s in stocks
            if s["volume"] >= min_volume
            and s["ltp"] >= min_price
            and abs(s["change_pct"]) >= min_change_pct
        ]
        stocks.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
        return stocks[:n]

    def get_top_gainers(self, n: int = 10) -> list:
        """Return top N gainers."""
        stocks = [s for s in self.active_movers.values() if s["change_pct"] > 0]
        stocks.sort(key=lambda x: x["change_pct"], reverse=True)
        return stocks[:n]

    def get_top_losers(self, n: int = 10) -> list:
        """Return top N losers."""
        stocks = [s for s in self.active_movers.values() if s["change_pct"] < 0]
        stocks.sort(key=lambda x: x["change_pct"])
        return stocks[:n]

    def get_sectors(self) -> list:
        """Return sector summaries."""
        return list(self.sectors_data.values())

    def get_all_stocks(self) -> list:
        """Return all heatmap stocks."""
        return list(self.active_movers.values())


# Global singleton
heatmap_poller = HeatmapPollerService()
