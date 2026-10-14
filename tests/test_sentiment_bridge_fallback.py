"""The sentiment endpoints degrade to a neutral window when news_stream is down."""

import socket

from backend.core.sentiment_bridge import SentimentBridge


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_unreachable_news_service_returns_neutral_window_not_an_error(monkeypatch):
    monkeypatch.setenv("NEWS_RSS_FALLBACK", "0")
    bridge = SentimentBridge(base_url=f"http://127.0.0.1:{_closed_port()}", enabled=True, timeout_ms=500)
    live = bridge.get_live_sentiment("USD/INR", 30)
    assert live["item_count"] == 0 and live["overall_sentiment"] == 0.0
    assert live["service"]["available"] is False
    assert live["service"]["last_error"]          # the reason is still reported

    articles = bridge.get_live_articles("USD/INR", 30, limit=8)   # was a 500 before
    assert isinstance(articles, dict)


RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Rupee falls to record low as dollar gains - Economic Times</title><link>https://example.com/a</link>
<pubDate>Wed, 14 Oct 2026 09:10:00 GMT</pubDate><source>Economic Times</source></item>
<item><title>Rupee rises 12 paise on RBI intervention - Mint</title><link>https://example.com/b</link>
<pubDate>Wed, 14 Oct 2026 08:00:00 GMT</pubDate><source>Mint</source></item>
<item><title>Football transfer news</title><link>https://example.com/c</link>
<pubDate>Wed, 14 Oct 2026 07:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_news_stream_down_falls_back_to_direct_rss_headlines(monkeypatch):
    from backend.core.news_fallback import RssHeadlines
    monkeypatch.delenv("NEWS_RSS_FALLBACK", raising=False)
    calls = []
    rss = RssHeadlines(feeds=["https://news.google.com/rss/x"], fetch=lambda url, t: calls.append(url) or RSS)
    bridge = SentimentBridge(base_url=f"http://127.0.0.1:{_closed_port()}", enabled=True, timeout_ms=300, rss=rss)

    live = bridge.get_live_sentiment("USD/INR", 30)
    assert live["status"] == "rss_fallback" and live["item_count"] == 2      # football dropped
    heads = [d["headline"] for d in live["top_drivers"]]
    assert heads[0] == "Rupee falls to record low as dollar gains"          # publisher suffix stripped
    assert live["top_drivers"][0]["sentiment"] > 0 > live["top_drivers"][1]["sentiment"]

    articles = bridge.get_live_articles("USD/INR", 30, limit=8)
    assert len(articles["items"]) == 2

    feed = bridge.get_feed_snapshot("USD/INR", limit=6)
    assert feed["items"][0]["provenance"] == "rss-direct"
    assert feed["items"][0]["source"] == "Economic Times"
    assert len(calls) == 1                                                  # cached between requests
