"""
Direct RSS headlines for when the news_stream service is not running.

The sentiment panel normally gets news from the semantic microservice
(services/semantic; SEMANTIC_SERVICE_URL, default http://127.0.0.1:9000). Without it the panel is
empty. This module fetches public RSS feeds directly (no key needed), keeps the
headlines relevant to a pair, and gives each a rough direction from a keyword
heuristic. It is labelled as a fallback everywhere it appears; it is not the
news_stream NLP/RAG pipeline.

Environment:
    NEWS_RSS_FALLBACK        "0" disables it (default on)
    NEWS_RSS_FEEDS           comma-separated RSS URLs (default: Google News
                             searches for the rupee/dollar, BBC Business)
    NEWS_RSS_CACHE_SECONDS   how long a fetch is reused (default 300)
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Callable, Dict, List, Optional
from urllib.request import Request, urlopen

DEFAULT_FEEDS = [
    "https://news.google.com/rss/search?q=rupee+dollar+OR+USD%2FINR+OR+RBI+forex&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=rupee+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"

# Headlines must mention one of these to count as relevant to the pair.
RELEVANT = {
    "USD/INR": r"rupee|\binr\b|usd/inr|usdinr|\brbi\b|reserve bank of india|forex|dollar|\bfed\b|federal reserve|"
               r"fii|fpi|india.*(inflation|cpi|gdp|trade deficit)|crude",
}
# Positive score = USD up / INR down (USD/INR rises); negative = INR strengthens.
_UP = [r"rupee (falls|fell|slips|slipped|weakens|weakened|drops|dropped|declines|hits? (a )?record low|slides|tumbles)",
       r"record low", r"dollar (gains|rises|rose|strengthens|firms|jumps)", r"outflows?", r"fii selling",
       r"crude (rises|jumps|surges)", r"rate hike", r"hawkish"]
_DOWN = [r"rupee (rises|rose|gains|gained|strengthens|strengthened|climbs|jumps|rallies|recovers)",
         r"dollar (falls|fell|slips|weakens|eases|drops)", r"inflows?", r"rbi (intervenes|intervention|sells dollars)",
         r"rate cut", r"dovish", r"crude (falls|slips|eases|drops)"]


def headline_direction(headline: str) -> float:
    """Keyword heuristic in [-1, 1] for USD/INR: + means rupee weaker."""
    text = headline.lower()
    up = sum(1 for p in _UP if re.search(p, text))
    down = sum(1 for p in _DOWN if re.search(p, text))
    if up == down:
        return 0.0
    return max(-1.0, min(1.0, 0.5 * (up - down)))


def _parse_rss(xml_bytes: bytes, feed_url: str) -> List[dict]:
    items = []
    root = ET.fromstring(xml_bytes)
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        source = (item.findtext("source") or "").strip()
        if not source:
            source = "BBC" if "bbc" in feed_url else ("Google News" if "news.google" in feed_url else "RSS")
        when = item.findtext("pubDate")
        try:
            ts = parsedate_to_datetime(when).astimezone(UTC) if when else datetime.now(UTC)
        except (TypeError, ValueError):
            ts = datetime.now(UTC)
        # Google News titles end with " - Publisher"
        if " - " in title and "news.google" in feed_url:
            title, _, pub = title.rpartition(" - ")
            source = source or pub
        items.append({"headline": title, "url": (item.findtext("link") or "").strip() or None,
                      "source": source, "timestamp": ts})
    return items


def _default_fetch(url: str, timeout_s: float) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": UA}), timeout=timeout_s) as resp:
        return resp.read()


class RssHeadlines:
    def __init__(self, feeds: Optional[List[str]] = None,
                 fetch: Callable[[str, float], bytes] = _default_fetch,
                 cache_seconds: Optional[float] = None, timeout_s: float = 4.0):
        env_feeds = [u.strip() for u in os.environ.get("NEWS_RSS_FEEDS", "").split(",") if u.strip()]
        self._feeds = feeds or env_feeds or DEFAULT_FEEDS
        self._fetch = fetch
        self._ttl = cache_seconds if cache_seconds is not None else float(os.environ.get("NEWS_RSS_CACHE_SECONDS", "300"))
        self._timeout = timeout_s
        self._lock = threading.Lock()
        self._cached: List[dict] = []
        self._fetched_at = 0.0
        self.last_error: Optional[str] = None

    @staticmethod
    def enabled() -> bool:
        return os.environ.get("NEWS_RSS_FALLBACK", "1").strip().lower() not in {"0", "false", "no", "off"}

    def _refresh(self) -> List[dict]:
        with self._lock:
            if self._cached and time.time() - self._fetched_at < self._ttl:
                return self._cached
            if not self._cached and self._fetched_at and time.time() - self._fetched_at < 30:
                return self._cached        # recent failure: don't hammer the network on every request
            items, errors = [], []
            for url in self._feeds:
                try:
                    items.extend(_parse_rss(self._fetch(url, self._timeout), url))
                except Exception as e:     # one dead feed must not hide the others
                    errors.append(f"{url.split('/')[2]}: {e}")
            seen, unique = set(), []
            for it in sorted(items, key=lambda x: x["timestamp"], reverse=True):
                key = re.sub(r"\W+", " ", it["headline"].lower()).strip()
                if key not in seen:
                    seen.add(key)
                    unique.append(it)
            self._cached = unique
            self._fetched_at = time.time()
            self.last_error = "; ".join(errors) or None
            return unique

    def for_pair(self, pair: str, limit: int = 12) -> List[dict]:
        """Relevant headlines for `pair` (e.g. "USD/INR"), newest first, with a direction score."""
        pattern = RELEVANT.get(pair) or re.escape(pair.split("/")[0].lower())
        out = []
        for it in self._refresh():
            if not re.search(pattern, it["headline"].lower()):
                continue
            score = headline_direction(it["headline"]) if pair == "USD/INR" else 0.0
            out.append({
                **it,
                "event_id": "rss-" + hashlib.sha1(f"{pair}|{it['headline']}".encode()).hexdigest()[:16],
                "sentiment": score,
                "category": "forex" if re.search(r"rupee|forex|dollar|usd", it["headline"].lower()) else "macro",
            })
            if len(out) >= limit:
                break
        return out
