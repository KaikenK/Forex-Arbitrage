"""The sentiment endpoints degrade to a neutral window when news_stream is down."""

import socket

from backend.core.sentiment_bridge import SentimentBridge


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_unreachable_news_service_returns_neutral_window_not_an_error():
    bridge = SentimentBridge(base_url=f"http://127.0.0.1:{_closed_port()}", enabled=True, timeout_ms=500)
    live = bridge.get_live_sentiment("USD/INR", 30)
    assert live["item_count"] == 0 and live["overall_sentiment"] == 0.0
    assert live["service"]["available"] is False
    assert live["service"]["last_error"]          # the reason is still reported

    articles = bridge.get_live_articles("USD/INR", 30, limit=8)   # was a 500 before
    assert isinstance(articles, dict)
