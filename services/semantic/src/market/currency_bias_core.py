"""H6 currency-bias formula — the single shared implementation (RESEARCH_TRACKER §6, 2026-10-25).

This file is copied byte-for-byte into the semantic service
(`services/semantic/src/market/currency_bias_core.py`); a test in each tree asserts the two
copies are identical, so research and the live service cannot compute different signals.
It therefore depends on the standard library only.

Locked definitions implemented here (quoted from the H6 entry):
  B_c,t   = sum w_i * s_i / sum w_i over headlines attributed to c with GDELT timestamp
            <= t - 15 min, s_i = FinBERT polarity,
            w_i = exp(-age/12 h) x FinBERT confidence x novelty_i x relevance_i,
            relevance_i = 1 if c is named explicitly in the headline, 0.5 if attributed only
            through a country/institution term.
  E_c,t   = the Option A surprise index for c: sign-oriented, exact-time, high-impact
            releases <= t, same 12 h decay.
  bias_c  = standardized B + standardized E (equal weights).
  pair    = bias_base - bias_quote.
  The signal uses the 24 h window the baseline clause calls "the same 24 h window".

Clauses the locked text leaves open are parameters here with no default; callers pass the
values a §6 entry decides (`research/semantic/currency_bias.py` UNRESOLVED).
"""
from __future__ import annotations

import bisect
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

DECAY_HOURS = 12.0
GDELT_LAG_MINUTES = 15.0
NEWS_WINDOW_HOURS = 24.0
RELEVANCE_EXPLICIT = 1.0
RELEVANCE_INDIRECT = 0.5
CURRENCIES: tuple[str, ...] = ("USD", "EUR", "GBP", "JPY", "CHF", "INR")

# ------------------------------------------------------------------ attribution terms
# Mirrors research/data/tagging.py (a research test asserts the tables are equal).
STRONG_CURRENCY_HINTS: dict[str, str] = {
    "ecb": "EUR", "eurozone": "EUR", "lagarde": "EUR", "draghi": "EUR",
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
WEAK_CURRENCY_HINTS: dict[str, str] = {
    "euro": "EUR", "euros": "EUR",
    "dollar": "USD", "dollars": "USD", "us treasury": "USD",
    "pound": "GBP", "pounds": "GBP",
}
ISO_CODES = frozenset({"USD", "EUR", "JPY", "GBP", "CHF", "AUD", "NZD", "CAD", "INR", "CNY"})
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
FALSE_FRIEND_PATTERNS: tuple[str, ...] = (
    "lottery", "raffle", "jackpot", "prize", "world cup", "premier league", "champions league",
    "transfer fee", "box office", "net worth", "salary", "fined", "sponsorship",
)

# "Named explicitly" = the currency's own name or ISO code. Every other hint is a country,
# region or institution term (ECB, eurozone, Fed, Powell, US Treasury, BoJ, RBI, ...) and
# earns relevance 0.5. This is the locked rule applied term by term, not a tuning choice.
EXPLICIT_CURRENCY_TERMS: frozenset[str] = frozenset({
    "greenback", "dollar", "dollars", "yen", "sterling", "pound", "pounds", "euro", "euros",
    "swiss franc", "rupee", "yuan", "renminbi", "aussie dollar", "australian dollar",
    "kiwi dollar", "new zealand dollar", "loonie", "canadian dollar",
})

_TOKEN_CACHE: dict[str, re.Pattern] = {}


def _pattern_for(phrase: str) -> re.Pattern:
    cached = _TOKEN_CACHE.get(phrase)
    if cached is None:
        cached = re.compile(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", re.IGNORECASE)
        _TOKEN_CACHE[phrase] = cached
    return cached


def _is_fx_relevant(text: str) -> bool:
    lowered = str(text).casefold()
    if any(pattern in lowered for pattern in FALSE_FRIEND_PATTERNS):
        return False
    return any(pattern in lowered for pattern in FX_CONTEXT_PATTERNS)


def tag_currencies(text: str) -> list[str]:
    """Same algorithm as research/data/tagging.tag_currencies (require_context=True)."""
    lowered = str(text).casefold()
    if any(pattern in lowered for pattern in FALSE_FRIEND_PATTERNS):
        return []
    found: list[str] = []
    for hint, code in STRONG_CURRENCY_HINTS.items():
        if code not in found and _pattern_for(hint).search(text):
            found.append(code)
    for code in ISO_CODES:
        if code not in found and re.search(rf"\b{code}\b", text):
            found.append(code)
    if _is_fx_relevant(text):
        for hint, code in WEAK_CURRENCY_HINTS.items():
            if code not in found and _pattern_for(hint).search(text):
                found.append(code)
    return found


def relevance(text: str, currency: str) -> float | None:
    """1.0 if `currency` is named explicitly, 0.5 if only via a country/institution term.

    None when no attribution term for `currency` appears in the text at all (the caller's
    attribution came from elsewhere); what to do then is an open clause.
    """
    if re.search(rf"\b{re.escape(currency)}\b", str(text)):
        return RELEVANCE_EXPLICIT
    hints = {**STRONG_CURRENCY_HINTS, **WEAK_CURRENCY_HINTS}
    indirect = False
    for hint, code in hints.items():
        if code != currency or not _pattern_for(hint).search(str(text)):
            continue
        if hint in EXPLICIT_CURRENCY_TERMS:
            return RELEVANCE_EXPLICIT
        indirect = True
    return RELEVANCE_INDIRECT if indirect else None


# ------------------------------------------------------------------ components

def decay(age_hours: float) -> float:
    return math.exp(-max(age_hours, 0.0) / DECAY_HOURS)


def headline_weight(age_hours: float, confidence: float, novelty: float, relevance_value: float) -> float:
    return decay(age_hours) * float(confidence) * float(novelty) * float(relevance_value)


@dataclass(frozen=True)
class NewsComponent:
    value: float | None          # B_c,t; None when the total weight is 0
    total_weight: float
    n_headlines: int
    effective_n: float           # (sum w)^2 / sum w^2
    agreement: float             # |sum w s| / sum w |s|; 1 = all one sign
    newest_age_hours: float | None
    contributors: tuple[int, ...]  # indices into the input lists, largest w*|s| first


def news_component(
    timestamps_s: Sequence[float],
    polarities: Sequence[float],
    confidences: Sequence[float],
    novelties: Sequence[float | None],
    relevances: Sequence[float | None],
    as_of_s: float,
    *,
    novelty_missing: str,
    relevance_unmatched: str,
    window_hours: float = NEWS_WINDOW_HOURS,
    lag_minutes: float = GDELT_LAG_MINUTES,
    max_contributors: int = 3,
) -> NewsComponent:
    """B_c,t over headlines in (as_of - window, as_of - lag]. Inputs sorted by timestamp.

    novelty_missing:     "drop_headline" (a headline with no novelty score is left out) |
                         "as_novel" (novelty 1.0: fewer than 3 prior stories = nothing similar).
    relevance_unmatched: "drop_headline" | "indirect" (treat as relevance 0.5).
    """
    if novelty_missing not in {"drop_headline", "as_novel"}:
        raise ValueError(f"unknown novelty_missing {novelty_missing!r}")
    if relevance_unmatched not in {"drop_headline", "indirect"}:
        raise ValueError(f"unknown relevance_unmatched {relevance_unmatched!r}")
    upper = as_of_s - lag_minutes * 60.0
    lower = as_of_s - window_hours * 3600.0
    lo = bisect.bisect_right(timestamps_s, lower)
    hi = bisect.bisect_right(timestamps_s, upper)
    total = weighted = squared = signed = absolute = 0.0
    newest: float | None = None
    used: list[tuple[float, int]] = []
    for i in range(lo, hi):
        novelty = novelties[i]
        if novelty is None:
            if novelty_missing == "drop_headline":
                continue
            novelty = 1.0
        rel = relevances[i]
        if rel is None:
            if relevance_unmatched == "drop_headline":
                continue
            rel = RELEVANCE_INDIRECT
        age_h = (as_of_s - timestamps_s[i]) / 3600.0
        w = headline_weight(age_h, confidences[i], novelty, rel)
        if w <= 0.0:
            continue
        s = float(polarities[i])
        total += w
        weighted += w * s
        squared += w * w
        signed += w * s
        absolute += w * abs(s)
        newest = age_h if newest is None else min(newest, age_h)
        used.append((w * abs(s), i))
    if total <= 0.0:
        return NewsComponent(None, 0.0, 0, 0.0, 0.0, None, ())
    used.sort(reverse=True)
    return NewsComponent(
        value=weighted / total, total_weight=total, n_headlines=len(used),
        effective_n=total * total / squared if squared > 0 else 0.0,
        agreement=abs(signed) / absolute if absolute > 0 else 0.0,
        newest_age_hours=newest,
        contributors=tuple(i for _, i in used[:max_contributors]),
    )


def event_component(
    timestamps_s: Sequence[float],
    oriented_surprises: Sequence[float],
    as_of_s: float,
    *,
    window_hours: float,
) -> float | None:
    """E_c,t: decayed mean of sign-oriented surprises released in (as_of - window, as_of]."""
    lo = bisect.bisect_right(timestamps_s, as_of_s - window_hours * 3600.0)
    hi = bisect.bisect_right(timestamps_s, as_of_s)
    total = weighted = 0.0
    for i in range(lo, hi):
        w = decay((as_of_s - timestamps_s[i]) / 3600.0)
        total += w
        weighted += w * float(oriented_surprises[i])
    return weighted / total if total > 0 else None


def standardize(value: float | None, mean: float, std: float) -> float | None:
    if value is None or not std or std <= 0 or math.isnan(std):
        return None
    return (value - mean) / std


def resolve_missing(z: float | None, raw_missing: bool, *, no_data_value: str, mean: float, std: float) -> float | None:
    """Apply the open `no_data_value` clause to a component with no data in its window.

    "raw_zero":  the raw component is 0 (no news is neutral news), then standardized.
    "z_zero":    the standardized component is 0 (contributes nothing).
    "drop_row":  the row has no signal (returns None; the caller drops it).
    """
    if not raw_missing:
        return z
    if no_data_value == "raw_zero":
        return standardize(0.0, mean, std)
    if no_data_value == "z_zero":
        return 0.0
    if no_data_value == "drop_row":
        return None
    raise ValueError(f"unknown no_data_value {no_data_value!r}")


def currency_bias(news_z: float | None, event_z: float | None) -> float | None:
    """Locked: standardized B + standardized E, equal weights. None if either is None."""
    if news_z is None or event_z is None:
        return None
    return news_z + event_z


def pair_bias(base_bias: float | None, quote_bias: float | None) -> float | None:
    if base_bias is None or quote_bias is None:
        return None
    return base_bias - quote_bias


# ------------------------------------------------------------------ live-only summaries
# Not part of the H6 test. Used by the service to describe a currency's signal.

def signal_confidence(component: NewsComponent, *, saturation_n: float = 5.0) -> float:
    """volume x agreement x freshness, in [0, 1].

    volume    = min(1, effective_n / saturation_n): how much independent-ish news there is.
    agreement = |sum w s| / sum w |s|: 1 when every weighted headline points the same way.
    freshness = exp(-age of the newest headline / 12 h), the signal's own decay.
    """
    if component.value is None or component.newest_age_hours is None:
        return 0.0
    volume = min(1.0, component.effective_n / saturation_n)
    return volume * component.agreement * decay(component.newest_age_hours)


def bias_label(strength: float | None, *, threshold: float) -> str:
    if strength is None:
        return "neutral"
    if strength > threshold:
        return "bullish"
    if strength < -threshold:
        return "bearish"
    return "neutral"
