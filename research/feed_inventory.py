"""Live inventory of news sources from a forex/finance point of view (RSS + Finnhub + Alpha Vantage).

For each candidate feed, one fetch, recording facts only:
  status / final URL       does it answer, does it redirect, does it parse as a feed
  items, newest age, rate  how much, how fresh, items per hour over the span it shows
  timestamp quality        share of items with a publish time; share with real clock times
                           (not date-only midnight); share carrying seconds
  FX relevance             share of items the research tagger attributes to a study currency
                           (USD, EUR, GBP, JPY, CHF, INR) — data/tagging.tag_currencies
  release-like             share whose title (or summary) names an indicator AND carries a
                           number (what the release extractor needs)
  summary numbers          share whose summary carries a number (agency feeds put figures there)

Finnhub / Alpha Vantage keys are read from capstone/news_stream/config.toml and never printed.
Writes artifacts/feed_inventory.json and paper/news_sources.md.

Run: python -m research.feed_inventory
"""
from __future__ import annotations

import json
import re
import ssl
import statistics
import time
import tomllib
import urllib.error
import urllib.request
from calendar import timegm
from datetime import UTC, datetime
from pathlib import Path

import certifi
import feedparser

from research.data.tagging import tag_currencies

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
PAPER = Path(__file__).resolve().parent / "paper"
NEWS_STREAM_CONFIG = Path(__file__).resolve().parents[2] / "news_stream" / "config.toml"
USER_AGENT = "Mozilla/5.0 (arbex-research feed inventory)"
STUDY = {"USD", "EUR", "GBP", "JPY", "CHF", "INR"}

# (group, name, url, in_live_config)
FEEDS: list[tuple[str, str, str, bool]] = [
    # FX-specialist outlets
    ("fx", "ForexLive news", "https://www.forexlive.com/feed/news", True),
    ("fx", "investingLive (ForexLive successor)", "https://investinglive.com/feed/", False),
    ("fx", "FXStreet news", "https://www.fxstreet.com/rss/news", True),
    ("fx", "FXStreet analysis", "https://www.fxstreet.com/rss/analysis", False),
    ("fx", "Action Forex", "https://www.actionforex.com/feed/", False),
    ("fx", "FXEmpire news", "https://www.fxempire.com/api/v1/en/articles/rss/news", False),
    ("fx", "Investing.com forex news", "https://www.investing.com/rss/news_1.rss", False),
    ("fx", "Investing.com economic indicators", "https://www.investing.com/rss/news_95.rss", False),
    ("fx", "Investing.com economy", "https://www.investing.com/rss/news_14.rss", False),
    ("fx", "MarketPulse (OANDA)", "https://www.marketpulse.com/feed/", False),
    ("fx", "Nasdaq currencies", "https://www.nasdaq.com/feed/rssoutbound?category=Currencies", False),
    # General finance
    ("finance", "Reuters business (legacy)", "https://feeds.reuters.com/reuters/businessNews", True),
    ("finance", "CNBC top news", "https://www.cnbc.com/id/100003114/device/rss/rss.html", True),
    ("finance", "CNBC economy", "https://www.cnbc.com/id/20910258/device/rss/rss.html", False),
    ("finance", "CNBC finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html", False),
    ("finance", "MarketWatch top stories", "https://feeds.content.dowjones.io/public/rss/mw_topstories", False),
    ("finance", "MarketWatch market pulse", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse", False),
    ("finance", "WSJ markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml", False),
    ("finance", "WSJ world", "https://feeds.a.dj.com/rss/RSSWorldNews.xml", False),
    ("finance", "FT home (UK)", "https://www.ft.com/rss/home/uk", False),
    ("finance", "FT markets", "https://www.ft.com/markets?format=rss", False),
    ("finance", "Bloomberg markets", "https://feeds.bloomberg.com/markets/news.rss", False),
    ("finance", "Bloomberg economics", "https://feeds.bloomberg.com/economics/news.rss", False),
    ("finance", "Yahoo Finance", "https://finance.yahoo.com/news/rssindex", False),
    ("finance", "BBC business", "http://feeds.bbci.co.uk/news/business/rss.xml", True),
    ("finance", "Guardian business", "https://www.theguardian.com/uk/business/rss", False),
    # India / INR
    ("india", "Economic Times markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", True),
    ("india", "Economic Times forex", "https://economictimes.indiatimes.com/markets/forex/rssfeeds/1150221130.cms", False),
    ("india", "Livemint markets", "https://www.livemint.com/rss/markets", True),
    ("india", "Business Standard markets", "https://www.business-standard.com/rss/markets-106.rss", True),
    ("india", "Moneycontrol markets", "https://www.moneycontrol.com/rss/markets.xml", True),
    ("india", "NDTV business", "https://www.ndtv.com/rss/business/latest.xml", True),
    # Central banks
    ("official", "Federal Reserve monetary policy", "https://www.federalreserve.gov/feeds/press_monetary.xml", False),
    ("official", "Federal Reserve all press", "https://www.federalreserve.gov/feeds/press_all.xml", False),
    ("official", "Federal Reserve speeches", "https://www.federalreserve.gov/feeds/speeches.xml", False),
    ("official", "ECB press", "https://www.ecb.europa.eu/rss/press.html", False),
    ("official", "ECB statistical press", "https://www.ecb.europa.eu/rss/statpress.html", False),
    ("official", "Bank of England news", "https://www.bankofengland.co.uk/rss/news", False),
    ("official", "Bank of Japan what's new", "https://www.boj.or.jp/en/rss/whatsnew.xml", False),
    ("official", "Swiss National Bank press", "https://www.snb.ch/public/en/rss/pressrel", False),
    ("official", "RBI press releases", "https://www.rbi.org.in/pressreleases_rss.xml", False),
    # Statistics agencies (release figures)
    ("agency", "BLS latest releases", "https://www.bls.gov/feed/bls_latest.rss", False),
    ("agency", "BLS employment situation", "https://www.bls.gov/feed/empsit.rss", False),
    ("agency", "BLS CPI", "https://www.bls.gov/feed/cpi.rss", False),
    ("agency", "BLS PPI", "https://www.bls.gov/feed/ppi.rss", False),
    ("agency", "BEA releases", "https://apps.bea.gov/rss/rss.xml", False),
    ("agency", "Census economic indicators", "https://www.census.gov/economic-indicators/indicator.xml", False),
    ("agency", "Eurostat euro indicators", "https://ec.europa.eu/eurostat/api/dissemination/catalogue/rss/en/statistics-update.rss", False),
    ("agency", "ONS release calendar", "https://www.ons.gov.uk/releasecalendar?rss", False),
    ("agency", "US Treasury press", "https://home.treasury.gov/system/files/rss/press-releases.xml", False),
]

_INDICATOR = re.compile(
    r"(?<![a-z])(cpi|ppi|pce|inflation|consumer prices?|payrolls?|non-?farm|jobless|unemployment|"
    r"retail sales|gdp|pmi|ism|jolts|earnings|claims|rate decision|interest rates?|bank rate|"
    r"refinancing rate|funds rate|consumer confidence|consumer sentiment|trade balance|durable goods)(?![a-z])",
    re.IGNORECASE)
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?\s*(?:%|percent|k\b|bp|bps|m\b|bn)|\d{1,3}(?:,\d{3})+", re.IGNORECASE)


# The system store on this machine lacks some intermediate certificates (ECB, SNB, Eurostat fail
# verification with it); certifi's bundle verifies them. Verification is never switched off.
_TLS = ssl.create_default_context(cafile=certifi.where())


def _fetch(url: str, timeout: float = 20.0) -> tuple[int | None, str, bytes | None, str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_TLS) as response:
            return response.status, response.geturl(), response.read(), None
    except urllib.error.HTTPError as exc:
        return exc.code, url, None, f"HTTP {exc.code}"
    except Exception as exc:                         # DNS, TLS, timeout
        return None, url, None, f"{type(exc).__name__}: {exc}"[:160]


def _entry_time(entry) -> tuple[float | None, bool, bool]:
    """(epoch seconds, has a real clock time, carries non-zero seconds)."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None, False, False
    epoch = float(timegm(parsed))
    clock = not (parsed.tm_hour == 0 and parsed.tm_min == 0 and parsed.tm_sec == 0)
    return epoch, clock, parsed.tm_sec != 0


def _share(values: list[bool]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def profile_items(items: list[dict], now: float) -> dict:
    """Facts about a list of items with keys title, summary, epoch, clock, seconds."""
    times = sorted(i["epoch"] for i in items if i["epoch"] is not None)
    span_h = (times[-1] - times[0]) / 3600 if len(times) >= 2 else None
    gaps = [b - a for a, b in zip(times, times[1:])]
    fx, release, summary_num = [], [], []
    for item in items:
        text = f"{item['title']} {item['summary']}"
        fx.append(bool(set(tag_currencies(text)) & STUDY))
        release.append(bool(_INDICATOR.search(text) and _NUMBER.search(text)))
        summary_num.append(bool(_NUMBER.search(item["summary"])))
    return {
        "items": len(items),
        "with_time": _share([i["epoch"] is not None for i in items]),
        "with_clock_time": _share([i["clock"] for i in items]),
        "with_seconds": _share([i["seconds"] for i in items]),
        "newest_age_min": None if not times else round((now - times[-1]) / 60, 1),
        "span_hours": None if span_h is None else round(span_h, 2),
        "items_per_hour": None if not span_h else round((len(times) - 1) / span_h, 2),
        "median_gap_min": None if not gaps else round(statistics.median(gaps) / 60, 1),
        "fx_relevant": _share(fx), "release_like": _share(release), "summary_has_number": _share(summary_num),
    }


def inventory_feed(group: str, name: str, url: str, live: bool, now: float) -> dict:
    status, final_url, body, error = _fetch(url)
    row = {"group": group, "name": name, "url": url, "in_live_config": live, "http_status": status,
           "final_url": final_url if final_url != url else None, "error": error}
    if body is None:
        row["state"] = "unreachable"
        return row
    feed = feedparser.parse(body)
    entries = list(getattr(feed, "entries", []))
    if not entries:
        row["state"] = "not_a_feed" if feed.get("bozo") else "empty"
        return row
    items = []
    for entry in entries:
        epoch, clock, seconds = _entry_time(entry)
        summary = re.sub(r"<[^>]+>", " ", str(entry.get("summary", "")))
        items.append({"title": str(entry.get("title", "")), "summary": summary, "epoch": epoch,
                      "clock": clock, "seconds": seconds})
    row.update(profile_items(items, now))
    row["state"] = "ok" if (row["newest_age_min"] is not None and row["newest_age_min"] < 7 * 24 * 60) else "stale"
    return row


def _keys() -> dict:
    try:
        config = tomllib.loads(NEWS_STREAM_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {"finnhub": (config.get("finnhub") or {}).get("api_key"),
            "alpha_vantage": (config.get("alpha_vantage") or {}).get("api_key")}


def inventory_finnhub(key: str | None, now: float) -> list[dict]:
    rows = []
    for category in ("general", "forex", "merger", "crypto"):
        row = {"group": "api", "name": f"Finnhub /news category={category}", "url": "https://finnhub.io/api/v1/news",
               "in_live_config": category in ("general", "forex")}
        if not key:
            rows.append({**row, "state": "no_key"})
            continue
        status, _, body, error = _fetch(f"https://finnhub.io/api/v1/news?category={category}&token={key}")
        row.update({"http_status": status, "error": error})
        try:
            data = json.loads(body) if body else None
        except ValueError:
            data = None
        if not isinstance(data, list):
            rows.append({**row, "state": "unreachable" if body is None else "unexpected_reply"})
            continue
        items = [{"title": str(a.get("headline", "")), "summary": str(a.get("summary", "")),
                  "epoch": float(a["datetime"]) if a.get("datetime") else None,
                  "clock": bool(a.get("datetime")) and datetime.fromtimestamp(a["datetime"], UTC).strftime("%H%M%S") != "000000",
                  "seconds": bool(a.get("datetime")) and int(a["datetime"]) % 60 != 0} for a in data]
        sources = sorted({str(a.get("source", "")) for a in data})
        rows.append({**row, **profile_items(items, now), "state": "ok", "sources": sources[:15]})
        time.sleep(1.1)                                 # free tier: 60 calls/min
    status, _, body, error = (None, "", None, "no key") if not key else _fetch(
        f"https://finnhub.io/api/v1/calendar/economic?token={key}")
    rows.append({"group": "api", "name": "Finnhub economic calendar", "url": "https://finnhub.io/api/v1/calendar/economic",
                 "in_live_config": False, "http_status": status, "error": error,
                 "state": "ok" if status == 200 and body and b"economicCalendar" in body else "not_available"})
    return rows


def inventory_alpha_vantage(key: str | None, now: float) -> list[dict]:
    row = {"group": "api", "name": "Alpha Vantage NEWS_SENTIMENT (economy_monetary, forex)",
           "url": "https://www.alphavantage.co/query?function=NEWS_SENTIMENT", "in_live_config": False}
    if not key:
        return [{**row, "state": "no_key"}]
    status, _, body, error = _fetch("https://www.alphavantage.co/query?function=NEWS_SENTIMENT"
                                    f"&topics=economy_monetary,economy_macro&limit=200&apikey={key}")
    row.update({"http_status": status, "error": error})
    try:
        data = json.loads(body) if body else {}
    except ValueError:
        data = {}
    feed = data.get("feed") if isinstance(data, dict) else None
    if not isinstance(feed, list):
        note = next((str(data[k])[:120] for k in ("Information", "Note", "Error Message") if isinstance(data, dict) and k in data), None)
        return [{**row, "state": "unexpected_reply", "error": error or note}]
    items = []
    for a in feed:
        try:
            epoch = datetime.strptime(a.get("time_published", ""), "%Y%m%dT%H%M%S").replace(tzinfo=UTC).timestamp()
        except ValueError:
            epoch = None
        items.append({"title": str(a.get("title", "")), "summary": str(a.get("summary", "")), "epoch": epoch,
                      "clock": epoch is not None, "seconds": epoch is not None and int(epoch) % 60 != 0})
    return [{**row, **profile_items(items, now), "state": "ok"}]


def _fmt(value, pct=False):
    if value is None:
        return "–"
    return f"{value:.0%}" if pct else f"{value:g}"


def write_markdown(rows: list[dict], checked_at: str) -> str:
    lines = [f"# News sources for FX — live inventory ({checked_at})", "",
             "One fetch per source; facts only. *FX* = share of items attributed to a study currency "
             "(USD, EUR, GBP, JPY, CHF, INR) by `data/tagging`; *release-like* = names an indicator and "
             "carries a number. Generated by `python -m research.feed_inventory`.", "",
             "| Group | Source | Live now | State | Items | Newest (min) | Items/h | Clock time | Seconds | FX | Release-like | Summary numbers |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        state = r["state"] + (f" ({r['error']})" if r.get("error") and r["state"] != "ok" else "")
        lines.append(f"| {r['group']} | {r['name']} | {'yes' if r['in_live_config'] else ''} | {state} | "
                     f"{_fmt(r.get('items'))} | {_fmt(r.get('newest_age_min'))} | {_fmt(r.get('items_per_hour'))} | "
                     f"{_fmt(r.get('with_clock_time'), True)} | {_fmt(r.get('with_seconds'), True)} | "
                     f"{_fmt(r.get('fx_relevant'), True)} | {_fmt(r.get('release_like'), True)} | "
                     f"{_fmt(r.get('summary_has_number'), True)} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    now = time.time()
    checked_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    rows = [inventory_feed(*feed, now) for feed in FEEDS]
    keys = _keys()
    rows += inventory_finnhub(keys.get("finnhub"), now)
    rows += inventory_alpha_vantage(keys.get("alpha_vantage"), now)
    ARTIFACTS.mkdir(exist_ok=True)
    (ARTIFACTS / "feed_inventory.json").write_text(json.dumps({"checked_at": checked_at, "sources": rows}, indent=2),
                                                   encoding="utf-8")
    PAPER.mkdir(exist_ok=True)
    (PAPER / "news_sources.md").write_text(write_markdown(rows, checked_at), encoding="utf-8")
    for r in rows:
        print(f"{r['state']:>16}  {r['name'][:44]:44}  items={r.get('items')}  newest={r.get('newest_age_min')}  "
              f"fx={r.get('fx_relevant')}  rel={r.get('release_like')}  {r.get('error') or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
