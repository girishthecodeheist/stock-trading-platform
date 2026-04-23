"""News sentiment engine - fetches headlines and performs NLP sentiment analysis."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List
from urllib.parse import quote_plus

from app import fyers_client

logger = logging.getLogger(__name__)


async def get_news_sentiment(symbol: str) -> Dict[str, Any]:
    """Fetch news and compute sentiment for a symbol."""
    clean_symbol = symbol.replace("NSE:", "").replace("BSE:", "").replace("-EQ", "").replace("-FUT", "").strip()

    headlines = await _fetch_news_headlines(clean_symbol)
    if not headlines:
        return {
            "headlines": [],
            "headline_count": 0,
            "avg_sentiment": 0.0,
            "sentiment_classification": "NEUTRAL",
            "bullish_count": 0,
            "bearish_count": 0,
            "neutral_count": 0,
            "sentiment_score": 0.0,
            "sentiment_signal": "NEUTRAL",
        }

    sentiments = []
    analyzed_headlines = []
    for headline in headlines:
        score = _analyze_sentiment(headline["title"])
        sentiments.append(score)
        analyzed_headlines.append({
            "title": headline["title"],
            "source": headline.get("source", "News"),
            "date": headline.get("date", ""),
            "sentiment_score": round(score, 4),
            "sentiment_label": _score_to_label(score),
        })

    avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0
    bullish = sum(1 for s in sentiments if s > 0.1)
    bearish = sum(1 for s in sentiments if s < -0.1)
    neutral = len(sentiments) - bullish - bearish

    # Normalize to -100 to +100 scale
    sentiment_score = round(avg_sentiment * 100, 2)

    return {
        "headlines": analyzed_headlines,
        "headline_count": len(analyzed_headlines),
        "avg_sentiment": round(avg_sentiment, 4),
        "sentiment_classification": _classify_sentiment(avg_sentiment),
        "bullish_count": bullish,
        "bearish_count": bearish,
        "neutral_count": neutral,
        "sentiment_score": sentiment_score,
        "sentiment_signal": _classify_sentiment(avg_sentiment),
    }


async def _fetch_news_headlines(symbol: str) -> List[Dict[str, str]]:
    """Fetch news headlines with a 3-tier source chain.

    Order: Fyers (when authenticated) -> yfinance -> Google News RSS. Each
    tier is tried only if the previous one returned no usable results, so
    the common "Fyers has news" path never incurs RSS/yfinance latency.
    """
    if fyers_client.is_authenticated():
        try:
            fyers_items = await fyers_client.get_news_async(symbol, limit=15)
            if fyers_items:
                return fyers_items
        except Exception as e:
            logger.debug(f"Fyers news fetch failed for {symbol}: {e}")

    yf_items = await _fetch_yfinance_news(symbol)
    if yf_items:
        return yf_items

    return await _fetch_google_news_rss(symbol)


async def _fetch_yfinance_news(symbol: str) -> List[Dict[str, str]]:
    """Fetch news headlines using yfinance news feed."""
    try:
        import yfinance as yf
        nse_symbol = f"{symbol}.NS"

        def _get_news():
            ticker = yf.Ticker(nse_symbol)
            try:
                news = ticker.news or []
            except Exception:
                news = []
            results = []
            for item in news[:15]:
                title = item.get("title", "")
                if not title:
                    continue
                pub_date = ""
                if "providerPublishTime" in item:
                    try:
                        pub_date = datetime.fromtimestamp(item["providerPublishTime"]).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        pass
                results.append({
                    "title": title,
                    "source": item.get("publisher", "Yahoo Finance"),
                    "date": pub_date,
                })
            return results

        return await asyncio.to_thread(_get_news)
    except Exception as e:
        logger.error(f"Error fetching yfinance news for {symbol}: {e}")
        return []


async def _fetch_google_news_rss(symbol: str, limit: int = 15) -> List[Dict[str, str]]:
    """Fallback: parse Google News RSS for NSE-tagged stories on ``symbol``.

    ``symbol`` is URL-encoded so real Indian tickers like ``M&M`` / ``L&T``
    (which contain `&`) and cleaned names with spaces (e.g. ``TATA MOTORS``)
    don't break the ``q=`` query parameter.
    """
    query = f"{quote_plus(symbol)}+NSE"
    url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN"

    def _parse():
        try:
            import feedparser
        except ImportError:
            logger.warning("feedparser not installed, skipping Google News RSS fallback")
            return []
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            logger.debug(f"Google News RSS parse failed for {symbol}: {e}")
            return []
        entries = getattr(feed, "entries", None) or []
        results: List[Dict[str, str]] = []
        for item in entries[:limit]:
            title = item.get("title", "")
            if not title:
                continue
            pub_date = ""
            published_parsed = item.get("published_parsed")
            if published_parsed:
                try:
                    pub_date = datetime(*published_parsed[:6]).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pub_date = item.get("published", "") or ""
            else:
                pub_date = item.get("published", "") or ""
            source = "Google News"
            src_obj = item.get("source")
            if isinstance(src_obj, dict):
                source = src_obj.get("title") or source
            results.append({
                "title": title,
                "source": source,
                "date": pub_date,
            })
        return results

    try:
        return await asyncio.to_thread(_parse)
    except Exception as e:
        logger.error(f"Error fetching Google News RSS for {symbol}: {e}")
        return []


def _analyze_sentiment(text: str) -> float:
    """Analyze sentiment of text using TextBlob. Returns -1.0 to 1.0."""
    try:
        from textblob import TextBlob
        blob = TextBlob(text)
        return blob.sentiment.polarity
    except ImportError:
        logger.warning("textblob not installed, using keyword-based sentiment")
        return _keyword_sentiment(text)
    except Exception:
        return 0.0


def _keyword_sentiment(text: str) -> float:
    """Fallback keyword-based sentiment analysis."""
    text_lower = text.lower()
    bullish_words = ["surge", "rally", "gain", "rise", "jump", "soar", "bull", "up", "high",
                     "profit", "growth", "strong", "positive", "upgrade", "outperform", "buy",
                     "record", "boost", "beat", "exceed", "optimistic", "breakout"]
    bearish_words = ["fall", "drop", "decline", "crash", "plunge", "bear", "down", "low",
                     "loss", "weak", "negative", "downgrade", "underperform", "sell",
                     "miss", "concern", "risk", "fear", "worry", "recession", "slowdown"]

    bull_count = sum(1 for w in bullish_words if w in text_lower)
    bear_count = sum(1 for w in bearish_words if w in text_lower)
    total = bull_count + bear_count
    if total == 0:
        return 0.0
    return (bull_count - bear_count) / total


def _score_to_label(score: float) -> str:
    if score > 0.1:
        return "BULLISH"
    elif score < -0.1:
        return "BEARISH"
    return "NEUTRAL"


def _classify_sentiment(avg: float) -> str:
    if avg > 0.3:
        return "STRONG BULLISH"
    elif avg > 0.1:
        return "BULLISH"
    elif avg > -0.1:
        return "NEUTRAL"
    elif avg > -0.3:
        return "BEARISH"
    else:
        return "STRONG BEARISH"
