"""Market-convention pair ordering and single-currency headline attribution."""
from __future__ import annotations

import pytest

from api.routes.feed import _detect_pair
from market.pairs import canonical_pair, pair_against_usd


@pytest.mark.parametrize(("a", "b", "pair"), [
    ("USD", "EUR", "EUR/USD"), ("EUR", "USD", "EUR/USD"), ("usd", "gbp", "GBP/USD"),
    ("JPY", "USD", "USD/JPY"), ("CHF", "USD", "USD/CHF"), ("INR", "USD", "USD/INR"),
    ("EUR", "JPY", "EUR/JPY"), ("GBP", "EUR", "EUR/GBP"),
])
def test_canonical_pair_follows_market_convention(a, b, pair):
    assert canonical_pair(a, b) == pair


def test_pair_against_usd_and_same_currency_rejected():
    assert pair_against_usd("EUR") == "EUR/USD"
    assert pair_against_usd("USD") is None
    with pytest.raises(ValueError):
        canonical_pair("USD", "USD")


@pytest.mark.parametrize(("headline", "pair"), [
    ("Euro slips as ECB signals pause", "EUR/USD"),           # was USD/EUR, then dropped
    ("Sterling firms after Bank of England holds", "GBP/USD"),  # was USD/GBP, then dropped
    ("Yen weakens past 150", "USD/JPY"),
    ("Dollar rallies, euro falls", "EUR/USD"),                # was USD/EUR
    ("France election weighs on bonds", "MACRO"),             # "franc" inside "France"
    ("Dollar gains on strong payrolls", "MACRO"),             # USD-only: filed under all study pairs
    ("USD/EUR quote", "EUR/USD"),
])
def test_detect_pair_single_currency_headlines(headline, pair):
    assert _detect_pair(headline) == pair
