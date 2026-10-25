"""Economic sign of each macro indicator, fixed a priori (2026-10-24, before any result).

Why this exists. A standardized surprise of +1σ means "higher than expected", but higher is
good news for a currency for most indicators (payrolls, CPI, retail sales) and bad news for
others (unemployment rate, jobless claims). Pooling surprises across indicators without
orienting them makes the two groups cancel and biases the pooled reaction toward zero.
Result R1 pooled raw surprises; it is confounded by exactly this and must be re-run.

Convention: `news_sign(country, event_name)` returns
  +1  higher-than-expected is good news for the issuing currency (it should appreciate),
  -1  higher-than-expected is bad news (it should depreciate),
  None  ambiguous for the currency — excluded from pooled estimates, never guessed.

The map encodes the standard economic prior used in the macro-announcement literature
(Andersen, Bollerslev, Diebold & Vega 2003): stronger activity, prices, wages and policy
rates support the currency; higher unemployment and more QE weaken it. It is fixed here,
before results, so it cannot be tuned to make a test pass. Per-indicator regressions, which
need no sign assumption at all, are reported alongside every pooled estimate as a check.
"""
from __future__ import annotations

# Indicators where a higher print is BAD news for the currency.
NEGATIVE_NEWS: frozenset[tuple[str, str]] = frozenset({
    ("United States", "Unemployment Claims"),
    ("United States", "Unemployment Rate"),
    ("United Kingdom", "Claimant Count Change"),
    # A larger asset-purchase programme than expected is dovish.
    ("United Kingdom", "Asset Purchase Facility"),
})

# Releases with no clear sign for the currency. Excluded from pooled estimates.
AMBIGUOUS: frozenset[tuple[str, str]] = frozenset({
    # An oil-market inventory print, not a statement about the US economy or the Fed.
    ("United States", "Crude Oil Inventories"),
})

CURRENCY_FOR_COUNTRY: dict[str, str] = {
    "United States": "USD",
    "Euro Area": "EUR",
    "United Kingdom": "GBP",
    "Japan": "JPY",
    "Switzerland": "CHF",
    "India": "INR",
}


def news_sign(country: str, event_name: str) -> int | None:
    """+1 / -1 / None — see module docstring."""
    key = (country, event_name)
    if key in AMBIGUOUS:
        return None
    if key in NEGATIVE_NEWS:
        return -1
    if country not in CURRENCY_FOR_COUNTRY:
        return None
    return 1


def currency_side(pair: str, currency: str) -> int | None:
    """+1 if `currency` is the pair's base (the pair rises when it strengthens), -1 if quote."""
    base, _, quote = pair.partition("/")
    if currency == base:
        return 1
    if currency == quote:
        return -1
    return None
