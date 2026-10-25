"""SSE news-feed route: real Finnhub + RSS headlines with synthetic fallback."""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

_logger = structlog.get_logger().bind(component="feed")

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
FINNHUB_NEWS_CATEGORIES = ("forex", "general")
UPSTREAM_PROVENANCE = "upstream"
SYNTHETIC_PROVENANCE = "synthetic"

# RSS feeds with financial / forex coverage
DEFAULT_RSS_URLS = (
    # Global
    "https://www.forexlive.com/feed/news",
    "https://feeds.reuters.com/reuters/businessNews",
    "http://feeds.bbci.co.uk/news/business/rss.xml",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    # India / INR focused
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.livemint.com/rss/markets",
    "https://www.business-standard.com/rss/markets-106.rss",
    "https://www.moneycontrol.com/rss/markets.xml",
    "https://www.ndtv.com/rss/business/latest.xml",
    # Forex-specific
    "https://www.fxstreet.com/rss/news",
)

# Only Finnhub/RSS items passing this keyword filter reach the SSE stream.
# Focused on USD/INR — dollar, rupee, India macro, and key global drivers.
FOREX_FILTER_WORDS = frozenset({
    # USD/INR direct
    "usd/inr", "rupee", "inr", "dollar", "rbi",
    # India macro & markets
    "india", "sensex", "nifty", "bse", "nse", "sebi", "modi",
    "fii", "fdi", "dii", "fpi", "outflow", "inflow", "capital flow",
    "monsoon", "upi", "gst", "india budget", "fiscal deficit",
    "india gdp", "india cpi", "india pmi", "india export", "india import",
    # US / Fed (major USD driver)
    "fed ", "federal reserve", "fomc", "powell", "rate decision",
    "interest rate", "inflation", "cpi", "us gdp", "payroll",
    "jobless", "treasury", "bond yield", "yield",
    # Forex / central bank broad
    "forex", "currency", "exchange rate", "central bank",
    "forex reserve", "trade balance", "trade deficit",
    "current account", "monetary policy",
    # Commodities affecting India
    "oil", "crude", "brent", "opec", "gold",
    # Geopolitical
    "war", "conflict", "sanction", "tariff", "geopolitical",
    "missile", "nuclear", "trade war",
    # Ratings
    "rating", "upgrade", "downgrade", "moody", "fitch", "s&p",
})

_PAIR_RE = re.compile(r"\b([A-Z]{3})[/\-]([A-Z]{3})\b")
# Only real currency/metal codes count as a pair. Without this guard the regex
# turns ordinary hyphenated words into fake pairs: "wait-and-see" -> AND/SEE,
# "mid-cap" -> MID/CAP, "has-not" -> HAS/NOT.
_SUPPORTED_CURRENCIES = frozenset({
    "USD", "EUR", "JPY", "GBP", "CHF", "AUD", "NZD", "CAD",
    "INR", "CNY", "XAU", "XAG",
})
_CURRENCY_HINTS: dict[str, str] = {
    "dollar": "USD", "euro": "EUR", "yen": "JPY", "pound": "GBP",
    "sterling": "GBP", "rupee": "INR", "franc": "CHF", "aussie": "AUD",
    "kiwi": "NZD", "loonie": "CAD", "fed ": "USD", "rbi": "INR",
    "ecb": "EUR", "boj": "JPY", "boe": "GBP", "india": "INR",
    "sensex": "INR", "nifty": "INR", "mumbai": "INR", "sebi": "INR",
    "modi": "INR", "das": "INR",
}

# ---------- synthetic fallback headlines ----------

SYNTHETIC_HEADLINES = [
    # ═══ RBI / Monetary Policy ═══
    {"headline": "RBI keeps repo rate unchanged at 6.50%, rupee steady", "source": "FINNHUB", "pair": "USD/INR", "category": "rate_decision"},
    {"headline": "RBI surprises with 25bp rate cut to support growth, rupee dips", "source": "FINNHUB", "pair": "USD/INR", "category": "rate_decision"},
    {"headline": "RBI hikes repo rate by 25bp to 6.75%, hawkish stance supports rupee", "source": "RSS", "pair": "USD/INR", "category": "rate_decision"},
    {"headline": "RBI Governor Das warns of imported inflation risks from rising crude", "source": "FINNHUB", "pair": "USD/INR", "category": "central_bank"},
    {"headline": "RBI shifts stance to neutral, signaling potential rate cuts ahead", "source": "RSS", "pair": "USD/INR", "category": "central_bank"},
    {"headline": "RBI announces special forex swap window of $5B to stabilize rupee", "source": "RSS", "pair": "USD/INR", "category": "intervention"},
    {"headline": "RBI sells $2.5B in spot market to defend rupee at 84 level", "source": "FINNHUB", "pair": "USD/INR", "category": "intervention"},
    # ═══ Fed / US Monetary Policy ═══
    {"headline": "Fed holds rates at 5.25-5.50%, signals higher for longer, dollar rallies", "source": "FINNHUB", "pair": "USD/INR", "category": "fed_decision"},
    {"headline": "Fed Chair Powell: 'We need greater confidence inflation is moving toward 2%'", "source": "RSS", "pair": "USD/INR", "category": "fed_decision"},
    {"headline": "FOMC signals three rate cuts in 2024, dollar weakens against rupee", "source": "FINNHUB", "pair": "USD/INR", "category": "fed_decision"},
    {"headline": "Fed surprises with 50bp emergency rate cut, dollar crashes vs INR", "source": "RSS", "pair": "USD/INR", "category": "fed_decision"},
    {"headline": "BREAKING: Federal Reserve minutes reveal deep divisions on rate path", "source": "RSS", "pair": "USD/INR", "category": "fed_decision"},
    # ═══ India Macro Data ═══
    {"headline": "India GDP growth accelerates to 7.8% in Q3, beating estimates", "source": "RSS", "pair": "USD/INR", "category": "macro_data"},
    {"headline": "India GDP growth slows to 5.4% in Q2, worst in 7 quarters", "source": "FINNHUB", "pair": "USD/INR", "category": "macro_data"},
    {"headline": "India CPI inflation cools to 4.85%, within RBI target band", "source": "FINNHUB", "pair": "USD/INR", "category": "macro_data"},
    {"headline": "India CPI inflation surges to 6.2%, above RBI tolerance band", "source": "RSS", "pair": "USD/INR", "category": "macro_data"},
    {"headline": "India's manufacturing PMI rises to 59.1, fastest pace in 16 months", "source": "FINNHUB", "pair": "USD/INR", "category": "macro_data"},
    {"headline": "India's services PMI rises to 61.2, expansion broadens", "source": "RSS", "pair": "USD/INR", "category": "macro_data"},
    {"headline": "Sensex crashes 1000 points on global sell-off, rupee weakens past 84", "source": "FINNHUB", "pair": "USD/INR", "category": "macro_data"},
    {"headline": "India's industrial production growth slumps to 0.5% in December", "source": "RSS", "pair": "USD/INR", "category": "macro_data"},
    # ═══ US Macro (impact on USD/INR) ═══
    {"headline": "US core CPI rises 0.4% MoM, above expectations, dollar strengthens vs INR", "source": "FINNHUB", "pair": "USD/INR", "category": "macro_data"},
    {"headline": "US non-farm payrolls surge to 303K, dollar rallies against rupee", "source": "FINNHUB", "pair": "USD/INR", "category": "macro_data"},
    {"headline": "US unemployment rate ticks up to 4.0%, weakening dollar bid", "source": "RSS", "pair": "USD/INR", "category": "macro_data"},
    {"headline": "US consumer confidence drops sharply in March survey", "source": "RSS", "pair": "USD/INR", "category": "macro_data"},
    # ═══ Capital Flows ═══
    {"headline": "FII outflows from Indian equities hit $1.2B this week, rupee pressured", "source": "FINNHUB", "pair": "USD/INR", "category": "capital_flows"},
    {"headline": "Foreign investors pour $2B into Indian equities in February", "source": "RSS", "pair": "USD/INR", "category": "capital_flows"},
    {"headline": "India's DII buying at record $3.1B offsets FII selling pressure", "source": "FINNHUB", "pair": "USD/INR", "category": "capital_flows"},
    {"headline": "India sovereign bond inclusion in global index attracts $890M inflows", "source": "RSS", "pair": "USD/INR", "category": "capital_flows"},
    {"headline": "FPI outflows cross $3B in 2024 so far, rupee under sustained pressure", "source": "FINNHUB", "pair": "USD/INR", "category": "capital_flows"},
    # ═══ Trade Data ═══
    {"headline": "India's trade deficit narrows to $15.6B in February on lower oil imports", "source": "FINNHUB", "pair": "USD/INR", "category": "trade_data"},
    {"headline": "India's current account deficit hits 3.5% of GDP in Q3", "source": "RSS", "pair": "USD/INR", "category": "trade_data"},
    {"headline": "India's exports rise 12% YoY, narrowing current account deficit", "source": "RSS", "pair": "USD/INR", "category": "trade_data"},
    # ═══ Reserves ═══
    {"headline": "India's forex reserves climb to $648B, providing cushion for rupee", "source": "FINNHUB", "pair": "USD/INR", "category": "reserves"},
    {"headline": "India's forex reserves drop $5B to $619B, lowest in 6 months", "source": "RSS", "pair": "USD/INR", "category": "reserves"},
    # ═══ Commodities / Oil ═══
    {"headline": "Brent crude tops $95 on OPEC+ cuts, pressure on India's import bill", "source": "FINNHUB", "pair": "USD/INR", "category": "commodity"},
    {"headline": "Crude oil slumps 4% on surprise US inventory build, easing INR pressure", "source": "RSS", "pair": "USD/INR", "category": "commodity"},
    {"headline": "Gold surges past $2,400 amid safe-haven demand, risk-off hits INR", "source": "FINNHUB", "pair": "USD/INR", "category": "commodity"},
    {"headline": "Oil prices crash 5%, easing India's trade deficit concerns, rupee firms", "source": "RSS", "pair": "USD/INR", "category": "commodity"},
    # ═══ Geopolitical ═══
    {"headline": "India-Pakistan border tensions escalate, defense spending fears weigh on rupee", "source": "FINNHUB", "pair": "USD/INR", "category": "geopolitical"},
    {"headline": "Middle East tensions escalate, oil prices spike 4%, risk-off in emerging markets", "source": "RSS", "pair": "USD/INR", "category": "geopolitical"},
    {"headline": "US-China trade tensions flare up, emerging market currencies including INR weaken", "source": "FINNHUB", "pair": "USD/INR", "category": "geopolitical"},
    {"headline": "Global risk sentiment improves as US-China trade deal hopes rise, INR rallies", "source": "RSS", "pair": "USD/INR", "category": "geopolitical"},
    {"headline": "Russia-Ukraine ceasefire talks progress, risk appetite lifts emerging markets", "source": "RSS", "pair": "USD/INR", "category": "geopolitical"},
    {"headline": "New US sanctions on Russia disrupt global energy flows, oil jumps 6%", "source": "FINNHUB", "pair": "USD/INR", "category": "geopolitical"},
    {"headline": "Trump threatens 25% tariffs on all Indian imports, rupee plunges 1.2%", "source": "FINNHUB", "pair": "USD/INR", "category": "geopolitical"},
    {"headline": "India-US bilateral trade talks yield positive results, rupee strengthens", "source": "RSS", "pair": "USD/INR", "category": "geopolitical"},
    # ═══ Famous Individuals / Leaders ═══
    {"headline": "PM Modi announces $100B infrastructure push, investor sentiment lifts rupee", "source": "RSS", "pair": "USD/INR", "category": "policy"},
    {"headline": "Finance Minister Sitharaman: India on track for 8% GDP growth this year", "source": "FINNHUB", "pair": "USD/INR", "category": "macro_data"},
    {"headline": "Fed Chair Powell signals patience on rate cuts, dollar strengthens vs rupee", "source": "FINNHUB", "pair": "USD/INR", "category": "fed_decision"},
    {"headline": "RBI Governor Malhotra pledges price stability as top priority", "source": "RSS", "pair": "USD/INR", "category": "central_bank"},
    {"headline": "Trump calls for weaker dollar, says strong greenback hurting US exports", "source": "FINNHUB", "pair": "USD/INR", "category": "policy"},
    {"headline": "Elon Musk announces $10B Tesla factory investment in India, FDI boost", "source": "RSS", "pair": "USD/INR", "category": "capital_flows"},
    {"headline": "IMF Chief Georgieva praises India's economic resilience, upgrades growth forecast", "source": "RSS", "pair": "USD/INR", "category": "macro_data"},
    {"headline": "Warren Buffett's Berkshire increases India portfolio allocation to $5B", "source": "FINNHUB", "pair": "USD/INR", "category": "capital_flows"},
    {"headline": "Janet Yellen warns of global recession risks, safe-haven dollar rallies", "source": "RSS", "pair": "USD/INR", "category": "macro_data"},
    {"headline": "World Bank President upgrades India GDP forecast to 7.5% for 2026", "source": "FINNHUB", "pair": "USD/INR", "category": "macro_data"},
    # ═══ Ratings / Sovereign ═══
    {"headline": "S&P upgrades India's outlook to positive from stable, rupee rallies", "source": "RSS", "pair": "USD/INR", "category": "rating"},
    {"headline": "Moody's warns of potential India downgrade if fiscal slippage persists", "source": "FINNHUB", "pair": "USD/INR", "category": "rating"},
    {"headline": "Global rating agencies affirm India's BBB- rating with stable outlook", "source": "RSS", "pair": "USD/INR", "category": "rating"},
    # ═══ Fiscal / Budget / Policy ═══
    {"headline": "India budget 2026: capex allocation increased to $130B, infrastructure push", "source": "RSS", "pair": "USD/INR", "category": "fiscal"},
    {"headline": "India's fiscal deficit at 5.6% of GDP, consolidation on track", "source": "FINNHUB", "pair": "USD/INR", "category": "fiscal"},
    {"headline": "India's GST collections hit record $25B in March, fiscal health improves", "source": "RSS", "pair": "USD/INR", "category": "fiscal"},
    {"headline": "SEBI tightens rules on FII derivative positions, markets dip", "source": "FINNHUB", "pair": "USD/INR", "category": "policy"},
    # ═══ Market Reactions / Rupee Moves ═══
    {"headline": "Indian rupee hits all-time low of 84.50 against the dollar", "source": "FINNHUB", "pair": "USD/INR", "category": "general"},
    {"headline": "Rupee rallies to 82.80 per dollar on strong FII inflows and oil drop", "source": "RSS", "pair": "USD/INR", "category": "general"},
    {"headline": "Nifty 50 rallies past 22,500 on strong quarterly earnings, rupee firms", "source": "FINNHUB", "pair": "USD/INR", "category": "general"},
    {"headline": "Dollar index hits 106 as traders trim rate cut bets, pressure on INR", "source": "RSS", "pair": "USD/INR", "category": "dxy"},
    {"headline": "US treasury 10Y yield breaks above 4.5%, capital flows out of India", "source": "FINNHUB", "pair": "USD/INR", "category": "yields"},
    {"headline": "India-US yield spread narrows to 250bp, reducing carry trade appeal for INR", "source": "RSS", "pair": "USD/INR", "category": "yields"},
    # ═══ Sector / Structural ═══
    {"headline": "India's IT sector exports surge, Infosys and TCS upgrade revenue guidance", "source": "RSS", "pair": "USD/INR", "category": "sector"},
    {"headline": "India's monsoon forecast above-normal, food inflation outlook eases", "source": "FINNHUB", "pair": "USD/INR", "category": "macro_data"},
    {"headline": "India attracts record FDI of $12B in Q1 from semiconductor investments", "source": "RSS", "pair": "USD/INR", "category": "capital_flows"},
]

ALL_SYNTHETIC = SYNTHETIC_HEADLINES


# ---------- config loading ----------

def _load_config() -> dict[str, Any]:
    """Read config.toml from the project root once per call."""
    config_path = Path(__file__).resolve().parents[3] / "config.toml"
    if not config_path.exists():
        return {}
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]
    try:
        with open(config_path, "rb") as fh:
            return tomllib.load(fh)
    except (OSError, ValueError):
        return {}


# ---------- helpers ----------

def _is_forex_relevant(text: str) -> bool:
    lowered = text.casefold()
    return any(word in lowered for word in FOREX_FILTER_WORDS)


def _detect_pair(text: str) -> str:
    """Best-effort forex pair detection from headline text."""
    for base_currency, quote_currency in _PAIR_RE.findall(text.upper()):
        if base_currency in _SUPPORTED_CURRENCIES and quote_currency in _SUPPORTED_CURRENCIES:
            return f"{base_currency}/{quote_currency}"
    lowered = text.casefold()
    currencies: list[str] = []
    for hint, code in _CURRENCY_HINTS.items():
        if hint in lowered and code not in currencies:
            currencies.append(code)
    if len(currencies) >= 2:
        return f"{currencies[0]}/{currencies[1]}"
    if len(currencies) == 1:
        return f"USD/{currencies[0]}" if currencies[0] != "USD" else "EUR/USD"
    return "MACRO"


def _detect_category(text: str) -> str:
    lowered = text.casefold()
    if any(w in lowered for w in ("rate decision", "rate hike", "rate cut", "interest rate")):
        return "rate_decision"
    if any(w in lowered for w in ("fed ", "federal reserve", "fomc")):
        return "fed_decision"
    if any(w in lowered for w in ("central bank", "ecb", "boj", "boe", "rbi", "monetary")):
        return "central_bank"
    if any(w in lowered for w in ("cpi", "inflation", "gdp", "pmi", "employment", "jobless", "payroll", "retail sales")):
        return "macro_data"
    if any(w in lowered for w in ("oil", "crude", "gold", "commodity", "brent", "opec", "energy")):
        return "commodity"
    if any(w in lowered for w in ("trade deficit", "trade surplus", "trade balance", "export", "import")):
        return "trade_data"
    if any(w in lowered for w in ("reserve", "forex reserve")):
        return "reserves"
    if any(w in lowered for w in ("fii", "fdi", "outflow", "inflow", "capital flow", "portfolio flow")):
        return "capital_flows"
    if any(w in lowered for w in ("war", "conflict", "sanction", "missile", "nuclear", "tension")):
        return "geopolitical"
    if any(w in lowered for w in ("tariff", "trade war", "regulation", "financial law", "reform")):
        return "policy"
    if any(w in lowered for w in ("bond yield", "treasury yield", "10-year", "yield curve")):
        return "yields"
    if any(w in lowered for w in ("dollar index", "dxy")):
        return "dxy"
    if any(w in lowered for w in ("budget", "fiscal deficit", "fiscal")):
        return "fiscal"
    if any(w in lowered for w in ("moody", "fitch", "s&p", "rating", "upgrade", "downgrade")):
        return "rating"
    if any(w in lowered for w in ("risk-off", "risk-on", "safe haven", "risk appetite", "crash", "correction")):
        return "risk_sentiment"
    if any(w in lowered for w in ("intervention", "forex swap", "forex window")):
        return "intervention"
    return "general"


def _event_with_feed_metadata(
    event: dict[str, Any],
    *,
    provenance: str,
    provider: str,
    feed_state: dict[str, object],
) -> dict[str, Any]:
    enriched_event = dict(event)
    last_upstream_item_at = feed_state.get("last_upstream_item_at")
    last_refill_at = feed_state.get("last_refill_at")
    enriched_event["provenance"] = provenance
    enriched_event["provider"] = provider
    enriched_event["feed_mode"] = "live" if provenance == UPSTREAM_PROVENANCE else "fallback"
    enriched_event["upstream_status"] = str(feed_state.get("upstream_status", "unknown"))
    enriched_event["upstream_note"] = str(feed_state.get("upstream_note", ""))
    enriched_event["last_upstream_item_at"] = (
        last_upstream_item_at.isoformat()
        if isinstance(last_upstream_item_at, datetime)
        else None
    )
    enriched_event["feed_checked_at"] = (
        last_refill_at.isoformat()
        if isinstance(last_refill_at, datetime)
        else None
    )
    return enriched_event


def _finnhub_to_event(article: dict[str, Any]) -> dict[str, Any]:
    headline = article.get("headline", "")
    source_name = article.get("source", "Finnhub")
    return {
        "headline": headline,
        "source": f"FINNHUB ({source_name})",
        "pair": _detect_pair(headline),
        "category": _detect_category(headline),
        "url": article.get("url", ""),
        "timestamp": datetime.now(UTC).isoformat(),
        "live": True,
    }


def _rss_to_event(entry: dict[str, Any]) -> dict[str, Any]:
    headline = entry.get("title", "")
    return {
        "headline": headline,
        "source": "RSS",
        "pair": _detect_pair(headline + " " + entry.get("summary", "")),
        "category": _detect_category(headline),
        "url": entry.get("link", ""),
        "timestamp": datetime.now(UTC).isoformat(),
        "live": True,
    }


# ---------- real fetchers ----------

async def _fetch_finnhub(api_key: str) -> list[dict[str, Any]]:
    """Fetch recent forex + general news from Finnhub API."""
    if not api_key:
        return []
    import httpx
    articles: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for category in FINNHUB_NEWS_CATEGORIES:
            try:
                resp = await client.get(
                    f"{FINNHUB_BASE_URL}/news",
                    params={"category": category, "token": api_key},
                )
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list):
                    articles.extend(data)
            except (httpx.HTTPError, ValueError) as exc:
                _logger.warning("finnhub_fetch_failed", category=category, error=str(exc))
    return articles


async def _fetch_rss(urls: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    """Fetch entries from RSS feeds via feedparser."""
    try:
        import feedparser  # type: ignore[import-untyped]
    except ImportError:
        _logger.warning("feedparser_not_installed")
        return []
    entries: list[dict[str, Any]] = []
    for url in urls:
        try:
            feed = await asyncio.to_thread(feedparser.parse, url)
            raw_entries = getattr(feed, "entries", [])
            for entry in raw_entries[:10]:
                entries.append(dict(entry) if not isinstance(entry, dict) else entry)
        except (OSError, RuntimeError, ValueError) as exc:
            _logger.warning("rss_fetch_failed", url=url, error=str(exc))
    return entries


# ---------- SSE generator ----------

async def generate_feed_events():
    """Infinite async generator yielding SSE-formatted real + synthetic news items."""
    config = _load_config()
    finnhub_key = config.get("finnhub", {}).get("api_key", "")

    buffer: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    seen_ids: set[str] = set()
    seen_headlines: set[str] = set()
    feed_state: dict[str, object] = {
        "upstream_status": "starting",
        "upstream_note": "Waiting for upstream headlines.",
        "last_upstream_item_at": None,
        "last_refill_at": None,
    }

    def _headline_key(text: str) -> str:
        """Normalize headline text into a dedup key."""
        normalized = re.sub(r"[^a-z0-9 ]", "", text.casefold()).strip()
        return hashlib.md5(normalized.encode()).hexdigest()

    async def _refill() -> None:
        """Background task: periodically fetch real news into the buffer."""
        while True:
            feed_state["last_refill_at"] = datetime.now(UTC)
            # Fetch Finnhub and RSS in parallel
            finnhub_result: list[dict[str, Any]] = []
            rss_result: list[dict[str, Any]] = []
            try:
                finnhub_result, rss_result = await asyncio.gather(
                    _fetch_finnhub(finnhub_key),
                    _fetch_rss(DEFAULT_RSS_URLS),
                )
            except Exception as exc:
                feed_state["upstream_status"] = "degraded"
                feed_state["upstream_note"] = f"Upstream fetch failed; synthetic fallback may appear. {exc}"
                _logger.warning("refill_gather_error", error=str(exc))

            relevant_items = 0

            # Process Finnhub articles
            for article in finnhub_result:
                aid = str(article.get("id", ""))
                headline = article.get("headline", "")
                if not headline:
                    continue
                hkey = _headline_key(headline)
                if hkey in seen_headlines:
                    continue
                if aid and aid in seen_ids:
                    continue
                if not _is_forex_relevant(headline + " " + article.get("summary", "")):
                    continue
                if aid:
                    seen_ids.add(aid)
                seen_headlines.add(hkey)
                real_event = _finnhub_to_event(article)
                feed_state["upstream_status"] = "live"
                feed_state["upstream_note"] = "Real upstream headlines are flowing into the feed."
                feed_state["last_upstream_item_at"] = datetime.now(UTC)
                relevant_items += 1
                await buffer.put(
                    _event_with_feed_metadata(
                        real_event,
                        provenance=UPSTREAM_PROVENANCE,
                        provider="finnhub",
                        feed_state=feed_state,
                    ),
                )

            # Process RSS entries
            for entry in rss_result:
                link = entry.get("link", "")
                title = entry.get("title", "")
                if not title:
                    continue
                hkey = _headline_key(title)
                if hkey in seen_headlines:
                    continue
                if link and link in seen_ids:
                    continue
                if not _is_forex_relevant(title + " " + entry.get("summary", "")):
                    continue
                if link:
                    seen_ids.add(link)
                seen_headlines.add(hkey)
                real_event = _rss_to_event(entry)
                feed_state["upstream_status"] = "live"
                feed_state["upstream_note"] = "Real upstream headlines are flowing into the feed."
                feed_state["last_upstream_item_at"] = datetime.now(UTC)
                relevant_items += 1
                await buffer.put(
                    _event_with_feed_metadata(
                        real_event,
                        provenance=UPSTREAM_PROVENANCE,
                        provider="rss",
                        feed_state=feed_state,
                    ),
                )

            if relevant_items == 0 and feed_state.get("upstream_status") != "degraded":
                feed_state["upstream_status"] = "fallback"
                feed_state["upstream_note"] = (
                    "No relevant upstream headlines were available in the latest refresh; "
                    "synthetic fallback may appear."
                )

            await asyncio.sleep(90)  # respect Finnhub rate limits

    refill_task = asyncio.create_task(_refill())

    synthetic_seen: set[int] = set()

    try:
        while True:
            try:
                event = await asyncio.wait_for(buffer.get(), timeout=5.0)
            except asyncio.TimeoutError:
                # No real news — cycle through USD/INR synthetic headlines
                available = [i for i in range(len(ALL_SYNTHETIC)) if i not in synthetic_seen]
                if not available:
                    synthetic_seen.clear()
                    available = list(range(len(ALL_SYNTHETIC)))
                idx = random.choice(available)
                synthetic_seen.add(idx)
                item = ALL_SYNTHETIC[idx]
                event = _event_with_feed_metadata(
                    dict(item, timestamp=datetime.now(UTC).isoformat(), live=True),
                    provenance=SYNTHETIC_PROVENANCE,
                    provider="synthetic",
                    feed_state=feed_state,
                )

            yield f"data: {json.dumps(event)}\n\n"
            await asyncio.sleep(random.uniform(4.0, 8.0))
    finally:
        refill_task.cancel()
