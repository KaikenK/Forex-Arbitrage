"""Market-convention currency-pair ordering.

A pair is quoted with the higher-priority currency as base: EUR/USD, GBP/USD, USD/JPY,
USD/CHF, USD/INR. Building a pair by "USD/<other>" turns a euro- or pound-only headline into
USD/EUR or USD/GBP, which no venue quotes and the backend allowlist drops, so those headlines
never reached EUR/USD or GBP/USD.
"""
from __future__ import annotations

PAIR_SEPARATOR = "/"
_PRIORITY = ("XAU", "XAG", "EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY", "INR", "CNY")
_RANK = {code: index for index, code in enumerate(_PRIORITY)}


def canonical_pair(first: str, second: str) -> str:
    """The market-convention pair for two currency codes, in either order."""
    a, b = first.strip().upper(), second.strip().upper()
    if a == b:
        raise ValueError(f"a pair needs two different currencies, got {a}/{b}")
    base, quote = sorted((a, b), key=lambda code: (_RANK.get(code, len(_PRIORITY)), code))
    return f"{base}{PAIR_SEPARATOR}{quote}"


def pair_against_usd(currency: str) -> str | None:
    """The USD pair a single-currency headline belongs to; None for USD itself."""
    code = currency.strip().upper()
    return None if code == "USD" else canonical_pair(code, "USD")
