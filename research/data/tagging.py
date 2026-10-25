"""Lightweight rule-based tagging of headline text into affected currencies + event categories.

Deliberately transparent/deterministic so the labels are auditable for the event study.
This is the *coarse* label used for stratification; fine sentiment/embeddings come later in the
NLP stage. Categories are multi-label (a headline can be both `central_bank` and `rate_decision`).

Precision matters more than recall here: a mistagged story injects an unrelated price path into
the event study, which biases estimates toward zero. Two guards do most of the work:

  1. Word-boundary matching. Naive substring matching produced systematic false positives —
     "franc" inside "France", "fed" inside "Federal Bureau", "euro" inside "Europe".
  2. A financial-context requirement. A currency word alone is not an FX event ("one million
     euro in lottery raffle"); `is_fx_relevant` demands a market/policy signal as well.
"""
from __future__ import annotations

import re

# Strong signals: terms that are essentially never used outside an FX/monetary context, so they
# identify an FX event on their own ("RUPEE ROUT sends distress signals" has no other cue).
STRONG_CURRENCY_HINTS: dict[str, str] = {
    "ecb": "EUR", "eurozone": "EUR", "lagarde": "EUR", "draghi": "EUR",
    # "fed" is safe here because word-boundary matching rejects it inside "Federal"/"federally";
    # only the standalone institution name matches.
    "fed": "USD", "greenback": "USD", "federal reserve": "USD", "fomc": "USD",
    "powell": "USD", "yellen": "USD",
    "yen": "JPY", "boj": "JPY", "bank of japan": "JPY", "ueda": "JPY", "kuroda": "JPY",
    "sterling": "GBP", "boe": "GBP", "bank of england": "GBP",
    "swiss franc": "CHF", "snb": "CHF", "swiss national bank": "CHF",
    "rba": "AUD", "aussie dollar": "AUD", "australian dollar": "AUD",
    "rbnz": "NZD", "kiwi dollar": "NZD", "new zealand dollar": "NZD",
    "loonie": "CAD", "boc": "CAD", "bank of canada": "CAD", "canadian dollar": "CAD",
    "rupee": "INR", "rbi": "INR", "reserve bank of india": "INR",
    "yuan": "CNY", "renminbi": "CNY", "pboc": "CNY",
}

# Weak signals: real currency words that also appear as plain money amounts ("five million
# euro", "a billion dollars"), so they only count alongside a market/policy signal.
WEAK_CURRENCY_HINTS: dict[str, str] = {
    "euro": "EUR", "euros": "EUR",
    "dollar": "USD", "dollars": "USD", "us treasury": "USD",
    "pound": "GBP", "pounds": "GBP",
}

CURRENCY_HINTS: dict[str, str] = {**STRONG_CURRENCY_HINTS, **WEAK_CURRENCY_HINTS}

# ISO codes written explicitly (USD, EUR, ...) are unambiguous on word boundaries.
ISO_CODES = frozenset({"USD", "EUR", "JPY", "GBP", "CHF", "AUD", "NZD", "CAD", "INR", "CNY"})

# A currency mention only counts as an FX event alongside one of these market/policy signals.
FX_CONTEXT_PATTERNS: tuple[str, ...] = (
    "exchange rate", "forex", "fx ", "currency", "currencies", "devalu", "apprecia",
    "deprecia", "central bank", "monetary policy", "interest rate", "rate decision",
    "rate hike", "rate cut", "rates", "basis points", "inflation", "cpi", "gdp",
    "trade deficit", "trade surplus", "trade balance", "tariff", "bond yield", "yields",
    "treasury", "stimulus", "quantitative", "hawkish", "dovish", "recession", "economy",
    "economic", "market", "markets", "investors", "traders", "rally", "slump",
    "strengthen", "weaken", "surge", "plunge", "capital flows", "reserves", "intervention",
    "unemployment", "payroll", "pmi", "fiscal", "budget", "sanction", "trade war", "growth",
)

# Phrases that look like a currency mention but are not: a currency amount, a place name,
# or an unrelated proper noun. Checked before currency matching.
FALSE_FRIEND_PATTERNS: tuple[str, ...] = (
    "lottery", "raffle", "jackpot", "prize", "world cup", "premier league", "champions league",
    "transfer fee", "box office", "net worth", "salary", "fined", "sponsorship",
)

_TOKEN_CACHE: dict[str, re.Pattern] = {}


def _pattern_for(phrase: str) -> re.Pattern:
    """Word-boundary matcher, so 'franc' never matches 'France'."""
    cached = _TOKEN_CACHE.get(phrase)
    if cached is None:
        cached = re.compile(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", re.IGNORECASE)
        _TOKEN_CACHE[phrase] = cached
    return cached


def is_fx_relevant(text: str) -> bool:
    """True when the text carries a market/policy signal, not just a currency word."""
    lowered = str(text).casefold()
    if any(pattern in lowered for pattern in FALSE_FRIEND_PATTERNS):
        return False
    return any(pattern in lowered for pattern in FX_CONTEXT_PATTERNS)

# affected non-USD currency -> the pair we study it in (majors are quoted vs USD)
PAIR_FOR_CURRENCY: dict[str, str] = {
    "EUR": "EUR/USD", "GBP": "GBP/USD", "AUD": "AUD/USD", "NZD": "NZD/USD",
    "JPY": "USD/JPY", "CHF": "USD/CHF", "CAD": "USD/CAD", "INR": "USD/INR", "CNY": "USD/CNY",
}

# A USD release (NFP, CPI, FOMC) moves every USD pair, so it has no single natural pair.
# Two defensible policies, selected explicitly rather than left implicit:
#   "primary" - study it in EUR/USD only. One observation per event, no cross-sectional
#               dependence, and EUR/USD is the most liquid pair (cleanest reaction).
#   "all"     - study it in every major. More observations, but the same event appears
#               k times, so standard errors MUST be clustered by event.
USD_PRIMARY_PAIR = "EUR/USD"
USD_ALL_PAIRS: tuple[str, ...] = ("EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CHF")
# USD/INR is this project's primary pair. It has no scheduled Indian releases with a free
# consensus, but it is a USD pair, so US macro surprises are exogenous shocks to it and
# carry its causal arm on their own.
USD_INR_PAIRS: tuple[str, ...] = ("USD/INR",)
# The 2026-10-24 study set (RESEARCH_TRACKER §6): the four majors with free intraday data.
# AUD/USD is deliberately absent. USD/INR is studied separately at daily resolution, since no
# free intraday USD/INR exists. A new policy name rather than redefining "all", so results
# produced under "all" stay reproducible.
USD_STUDY_PAIRS: tuple[str, ...] = ("EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF")
INTRADAY_STUDY_PAIRS: frozenset[str] = frozenset(USD_STUDY_PAIRS)
DAILY_STUDY_PAIRS: frozenset[str] = frozenset({"USD/INR"})

# (label, keyword patterns) — first-match-any, multi-label
CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("rate_decision", ("rate decision", "rate hike", "rate cut", "raises rates", "cuts rates",
                       "basis points", "bps", "hikes rates", "holds rates", "repo rate")),
    ("monetary_policy", ("monetary policy", "quantitative", "hawkish", "dovish", "balance sheet",
                         "tapering", "forward guidance")),
    ("central_bank", ("central bank", "federal reserve", "fomc", " ecb ", " boj", " boe",
                      " rba", " snb", " rbi", "powell", "lagarde", "governor")),
    ("inflation", ("inflation", "cpi", "ppi", "price index", "deflation", "core cpi")),
    ("employment", ("payroll", "nonfarm", "non-farm", "unemployment", "jobless", "jobs report",
                    "labour market", "labor market")),
    ("growth", ("gdp", "recession", "pmi", "industrial production", "retail sales",
                "economic growth")),
    ("trade", ("trade balance", "trade deficit", "trade surplus", "tariff", "current account",
               "exports", "imports")),
    ("geopolitical", ("war", "conflict", "sanction", "missile", "invasion", "tension",
                      "geopolitical", "election", "coup")),
    ("commodity", ("oil", "crude", "brent", "opec", "gold", "natural gas")),
    ("rating", ("downgrade", "upgrade", "moody", "fitch", "credit rating", "sovereign rating")),
    ("intervention", ("intervention", "forex swap", "currency intervention", "defends the")),
]

_WORD = re.compile(r"[a-z][a-z\-']+")


def tag_currencies(text: str, *, require_context: bool = True) -> list[str]:
    """Currencies affected by the text. Empty when it is not an FX-relevant story.

    Strong signals (rupee, yen, ECB, ...) and explicit ISO codes stand alone. Weak signals
    (euro, dollar, pound) also need a market/policy cue, since they double as money amounts.
    """
    lowered = str(text).casefold()
    if require_context and any(pattern in lowered for pattern in FALSE_FRIEND_PATTERNS):
        return []

    found: list[str] = []
    for hint, code in STRONG_CURRENCY_HINTS.items():
        if code not in found and _pattern_for(hint).search(text):
            found.append(code)
    for code in ISO_CODES:
        if code not in found and re.search(rf"\b{code}\b", text):
            found.append(code)

    if not require_context or is_fx_relevant(text):
        for hint, code in WEAK_CURRENCY_HINTS.items():
            if code not in found and _pattern_for(hint).search(text):
                found.append(code)
    return found


def tag_categories(text: str) -> list[str]:
    lowered = f" {text.casefold()} "
    labels: list[str] = []
    for label, patterns in CATEGORY_RULES:
        if any(pattern in lowered for pattern in patterns) and label not in labels:
            labels.append(label)
    return labels


def currencies_to_pairs(currencies: list[str], *, usd_policy: str = "none") -> list[str]:
    """Map affected currencies to the pairs they are studied in.

    Non-USD currencies have a natural pair (EUR -> EUR/USD). USD does not, since a USD release
    moves every USD pair, so `usd_policy` decides explicitly:
      "none"    - drop USD-only events (default; preserves prior behaviour for GDELT news,
                  where a USD-only headline is usually too vague to attribute)
      "primary" - study USD events in EUR/USD only
      "inr"     - study USD events in USD/INR (this project's primary pair)
      "all"     - study USD events in every major (requires event-clustered standard errors)
      "study"   - the 2026-10-24 study set: USD events in the four intraday majors, and
                  non-USD currencies only when their pair is one of them (AUD, NZD, CAD and
                  CNY releases are dropped). Requires event-clustered standard errors.
    """
    pairs: list[str] = []
    for code in currencies:
        pair = PAIR_FOR_CURRENCY.get(code)
        if pair is not None:
            if usd_policy == "study" and pair not in INTRADAY_STUDY_PAIRS:
                continue
            if pair not in pairs:
                pairs.append(pair)
        elif code == "USD":
            if usd_policy == "primary":
                candidates: tuple[str, ...] = (USD_PRIMARY_PAIR,)
            elif usd_policy == "inr":
                candidates = USD_INR_PAIRS
            elif usd_policy == "all":
                candidates = USD_ALL_PAIRS
            elif usd_policy == "study":
                candidates = USD_STUDY_PAIRS
            else:
                candidates = ()
            for candidate in candidates:
                if candidate not in pairs:
                    pairs.append(candidate)
    return pairs
