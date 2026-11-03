"""The /feed stream closes its generator (and so its upstream poller) when the client leaves."""
from __future__ import annotations

import asyncio

import pytest

import serve


@pytest.mark.asyncio
async def test_sse_closes_generator_when_client_disconnects(monkeypatch):
    closed = asyncio.Event()
    sent: list[dict] = []
    first_chunk = asyncio.Event()

    async def endless_feed():
        try:
            while True:
                yield ": keepalive\n\n"
                await asyncio.sleep(0.01)
        finally:
            closed.set()

    async def send(message):
        sent.append(message)
        if message.get("body"):
            first_chunk.set()

    async def receive():
        await first_chunk.wait()
        return {"type": "http.disconnect"}

    monkeypatch.setattr(serve, "generate_feed_events", endless_feed)
    await asyncio.wait_for(serve._handle_sse(send, receive), timeout=2.0)

    assert closed.is_set()
    assert sent[-1] == {"type": "http.response.body", "body": b"", "more_body": False}


@pytest.mark.asyncio
async def test_sse_without_receive_still_streams(monkeypatch):
    async def two_items():
        yield "data: {}\n\n"
        yield "data: {}\n\n"

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    monkeypatch.setattr(serve, "generate_feed_events", two_items)
    await serve._handle_sse(send)
    assert [m.get("body") for m in sent[1:]] == [b"data: {}\n\n", b"data: {}\n\n", b""]
