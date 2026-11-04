"""C38: RSS feeds are fetched concurrently on one shared TLS context; order and failures as before."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from api.routes import feed

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>{title} one</title><link>https://x/{title}/1</link></item>
<item><title>{title} two</title><link>https://x/{title}/2</link></item></channel></rss>"""


@pytest.mark.asyncio
async def test_feeds_are_fetched_concurrently_in_order_and_failures_are_skipped(monkeypatch):
    in_flight = peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        name = request.url.host.split(".")[0]
        if name == "busy":
            return httpx.Response(429)
        return httpx.Response(200, text=RSS.format(title=name))

    real_client = httpx.AsyncClient
    seen_verify = []

    def client(**kwargs):
        seen_verify.append(kwargs.get("verify"))
        return real_client(transport=httpx.MockTransport(handler), **{k: v for k, v in kwargs.items() if k != "verify"})

    monkeypatch.setattr(httpx, "AsyncClient", client)
    urls = [f"https://{name}.example/rss" for name in ("alpha", "busy", "gamma", "delta", "eps", "zeta", "eta", "theta")]
    entries = await feed._fetch_rss(urls)
    titles = [e["title"] for e in entries]
    assert titles[:4] == ["alpha one", "alpha two", "gamma one", "gamma two"] and "busy one" not in titles
    assert len(titles) == 14 and 1 < peak <= feed.RSS_CONCURRENCY
    assert seen_verify == [feed._SSL_CONTEXT or True]
