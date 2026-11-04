"""Headline → scheduled economic release (currency, indicator, actual, forecast, surprise).

An article reporting a release ("US CPI rises 0.4% in March, above forecasts") is matched to
the live ForexFactory calendar row it reports: same currency, an indicator whose keywords
appear in the headline, and a release time within RELEASE_MATCH_WINDOW before publication.
The calendar supplies the forecast and the exact release time; the headline supplies the
actual. The standardized surprise uses the research σ for that (currency, indicator), so a
live z means the same as in the walk-forward evaluation (research/run_reaction_eval.py).

It abstains whenever any step is ambiguous: no currency, no calendar row in the window, two
rows that fit equally well, or no number with the forecast's unit. A wrong surprise is worse
than none — the scorer then falls back to the text model.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

RELEASE_MATCH_WINDOW = timedelta(minutes=90)
PUBLISH_CLOCK_SKEW = timedelta(minutes=5)

# Country / institution words → currency. Word-boundary matched, case-insensitive.
CURRENCY_TERMS: dict[str, tuple[str, ...]] = {
    "USD": ("us", "u.s.", "u.s", "united states", "american", "america's", "fed", "federal reserve",
            "fomc", "powell", "treasury", "dollar", "greenback"),
    "EUR": ("euro zone", "eurozone", "euro area", "ecb", "lagarde", "euro", "german", "germany",
            "french", "france", "bloc"),
    "GBP": ("uk", "u.k.", "britain", "british", "bank of england", "boe", "sterling", "pound"),
    "JPY": ("japan", "japanese", "boj", "bank of japan", "yen", "tokyo"),
    "CHF": ("swiss", "switzerland", "snb", "franc"),
}

# Indicator keywords, matched against both the headline and the calendar event name. A
# calendar row is a candidate when one of its families is mentioned in the headline.
FAMILIES: dict[str, tuple[str, ...]] = {
    "cpi": ("cpi", "consumer price", "consumer prices", "inflation"),
    "ppi": ("ppi", "producer price", "producer prices", "wholesale prices"),
    "pce": ("pce",),
    "payrolls": ("non-farm", "nonfarm", "payrolls", "payroll", "jobs report", "employers added",
                 "economy added", "jobs added"),
    "adp": ("adp",),
    "claims": ("jobless claims", "unemployment claims", "initial claims", "claimant count"),
    "unemployment": ("unemployment rate", "jobless rate"),
    "earnings": ("hourly earnings", "wage growth", "average earnings", "wages"),
    "retail": ("retail sales",),
    "gdp": ("gdp", "economic growth", "economy grew", "economy shrank", "economy contracted"),
    "pmi": ("pmi", "purchasing managers"),
    "ism": ("ism",),
    "confidence": ("consumer confidence",),
    "sentiment": ("consumer sentiment",),
    "rate": ("rate decision", "interest rate", "interest rates", "funds rate", "bank rate",
             "refinancing rate", "policy rate", "raises rates", "cuts rates", "holds rates",
             "keeps rates", "rate hike", "rate cut"),
    "durables": ("durable goods",),
    "jolts": ("jolts", "job openings"),
}

# Calendar event name → families (by keyword in the event title).
_EVENT_FAMILY_RULES: tuple[tuple[str, str], ...] = (
    ("core pce", "pce"), ("pce", "pce"), ("cpi", "cpi"), ("ppi", "ppi"),
    ("adp", "adp"), ("non-farm", "payrolls"), ("claims", "claims"), ("claimant", "claims"),
    ("unemployment rate", "unemployment"), ("earnings", "earnings"), ("retail sales", "retail"),
    ("gdp", "gdp"), ("ism", "ism"), ("pmi", "pmi"), ("consumer confidence", "confidence"),
    ("consumer sentiment", "sentiment"), ("funds rate", "rate"), ("bank rate", "rate"),
    ("refinancing rate", "rate"), ("policy rate", "rate"), ("durable goods", "durables"),
    ("jolts", "jolts"),
)

# Families that name one survey or compiler. A calendar row with one is matched only by a
# headline that names it too, and the other way round: "S&P Global manufacturing PMI" and
# "ISM manufacturing PMI" are both "pmi", but only the second reports the ISM release.
PROVIDER_FAMILIES = frozenset({"ism", "adp"})

_YOY = re.compile(r"(?<![a-z])(y/y|yoy|year[- ]on[- ]year|year[- ]over[- ]year|annual|annually|from a year|"
                  r"a year earlier|12 months)(?![a-z])", re.IGNORECASE)
_MOM = re.compile(r"(?<![a-z])(m/m|mom|month[- ]on[- ]month|monthly|from the previous month|"
                  r"from a month|last month)(?![a-z])", re.IGNORECASE)
_CORE = re.compile(r"(?<![a-z])(core|excluding food and energy|ex[- ]food|ex[- ]autos|excluding autos)(?![a-z])",
                   re.IGNORECASE)
_NUMBER = re.compile(r"(?<![\w.])([-+−]?\d{1,3}(?:,\d{3})+|[-+−]?\d+(?:\.\d+)?)\s*"
                     r"(%|percent|per cent|k\b|thousand|m\b|mln|million|b\b|bln|billion)?", re.IGNORECASE)
_UNIT_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mln": 1e6, "million": 1e6,
               "b": 1e9, "bln": 1e9, "billion": 1e9}


def _contains(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def headline_currencies(text: str) -> list[str]:
    lowered = text.casefold()
    return [c for c, terms in CURRENCY_TERMS.items() if any(_contains(lowered, t) for t in terms)]


def headline_families(text: str) -> set[str]:
    lowered = text.casefold()
    return {family for family, words in FAMILIES.items() if any(_contains(lowered, w) for w in words)}


def event_families(event_name: str) -> set[str]:
    lowered = event_name.casefold()
    found = {family for key, family in _EVENT_FAMILY_RULES if key in lowered}
    if "pce" in found:
        found.discard("cpi")
    return found


@dataclass(frozen=True)
class ParsedValue:
    value: float          # in base units (percent points for %, counts for K/M/B, raw otherwise)
    unit: str             # "%", "count" or ""


def parse_calendar_value(raw: str) -> ParsedValue | None:
    """ForexFactory value strings: '0.3%', '-0.1%', '180K', '1.2M', '52.3', '<0.10%'."""
    text = raw.strip().replace("<", "").replace(">", "")
    m = re.fullmatch(r"([-+−]?\d+(?:\.\d+)?)\s*(%|K|M|B|T)?", text, re.IGNORECASE)
    if not m:
        return None
    value = float(m.group(1).replace("−", "-"))
    suffix = (m.group(2) or "").upper()
    if suffix == "%":
        return ParsedValue(value, "%")
    if suffix in {"K", "M", "B", "T"}:
        return ParsedValue(value * {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[suffix], "count")
    return ParsedValue(value, "")


_RANGE_DASH = re.compile(r"(?<=[\d%])\s*[-–]\s*(?=\d)")
# A policy-rate range ("5.25%-5.50%", "5.25% to 5.50%") is reported by the calendar as its upper bound.
_RATE_RANGE = re.compile(r"\d+(?:\.\d+)?\s*%?\s*(?:[-–]|to)\s*(\d+(?:\.\d+)?)\s*%")


# A number introduced by one of these is a comparison figure (consensus, prior month), not the
# actual: "+0.5% vs +0.3% expected", "after rising 0.2 percent", "from 4.2%", "prior 0.2%".
_REFERENCE = re.compile(r"(?<![a-z])(vs\.?|versus|expected|exp\.?|forecast|consensus|estimates?|est\.?|"
                        r"prior|previous|prev\.?|revised|after|from|last)(?![a-z])[^0-9]{0,18}$", re.IGNORECASE)

# A falling verb directly before an unsigned number makes it a negative change: "retail sales
# fall 0.3%" is -0.3, which used to be read as +0.3 and flipped the surprise. Only directly before
# (optionally "by"): "falls to 4.1%" reports a level and keeps its sign.
_FALLING = re.compile(r"(?<![a-z])(falls?|fell|falling|declines?|declined|declining|drops?|dropped|dropping|"
                      r"slips?|slipped|slides?|slid|dips?|dipped|contracts?|contracted|contracting|"
                      r"shrinks?|shrank|shrunk|decreases?|decreased|plunges?|plunged|tumbles?|tumbled)"
                      r"(?:\s+by)?\s+$", re.IGNORECASE)


@dataclass(frozen=True)
class FoundNumber:
    value: float
    unit: str
    reference: bool       # introduced by a comparison word (see _REFERENCE)


def headline_numbers(text: str) -> list[ParsedValue]:
    return [ParsedValue(n.value, n.unit) for n in find_numbers(text)]


def find_numbers(text: str) -> list[FoundNumber]:
    """Numbers in reading order, each with its unit and whether a comparison word introduces it."""
    out: list[FoundNumber] = []
    text = _RATE_RANGE.sub(lambda m: f"{m.group(1)}%", text)
    text = _RANGE_DASH.sub(" to ", text)          # any other "a-b" is a range, not minus b
    for m in _NUMBER.finditer(text):
        raw, unit = m.group(1), m.group(2) or ""
        number = float(raw.replace(",", "").replace("−", "-"))
        if raw[0] not in "-+−" and _FALLING.search(text[max(0, m.start() - 24):m.start()]):
            number = -number
        unit_l = unit.casefold()
        if unit_l in {"%", "percent", "per cent"}:
            value, kind = number, "%"
        elif unit_l in _UNIT_SCALE:
            value, kind = number * _UNIT_SCALE[unit_l], "count"
        elif "," in raw:
            value, kind = number, "count"                   # "254,000 jobs"
        else:
            value, kind = number, ""
        out.append(FoundNumber(value, kind, bool(_REFERENCE.search(text[max(0, m.start() - 24):m.start()]))))
    return out


@dataclass(frozen=True)
class CalendarRow:
    currency: str
    event: str
    utc: datetime
    forecast: str
    impact: str


@dataclass(frozen=True)
class ReleaseMatch:
    currency: str
    event: str
    release_utc: datetime
    actual: float
    forecast: float
    unit: str
    surprise: float
    z: float | None              # None when no research σ exists for this indicator
    minutes_since_release: float


def _format_score(event: str, text: str) -> int:
    """+1 when the headline's y/y, m/m or core cue agrees with the event name, −1 when it contradicts."""
    name = event.casefold()
    score = 0
    if _YOY.search(text):
        score += 1 if ("y/y" in name or "3m/y" in name) else -1
    if _MOM.search(text):
        score += 1 if "m/m" in name else -1
    core_in_text, core_in_name = bool(_CORE.search(text)), "core" in name
    score += 1 if core_in_text == core_in_name else -1
    return score


def _pick_actual(numbers: Sequence[FoundNumber], forecast: ParsedValue, sigma: float | None) -> float | None:
    """The first number in reading order that has the forecast's unit, is not a comparison figure,
    and implies a plausible surprise (|z| ≤ MAX_PLAUSIBLE_Z when σ is known). Reports lead with the
    actual ("rose 0.5%, after 0.2%"; "+0.5% vs +0.3% expected"), so order beats closeness to the
    forecast, which would systematically pick the comparison figure and shrink surprises."""
    def candidates(allow_reference: bool) -> list[float]:
        values = []
        for n in numbers:
            if n.reference and not allow_reference:
                continue
            if n.unit == forecast.unit:
                values.append(n.value)
            elif forecast.unit == "count" and n.unit == "" and abs(n.value) >= 10:
                values.append(n.value * 1e3)                 # "payrolls 254" in thousands
        return values

    for allow_reference in (False, True):
        for value in candidates(allow_reference):
            if sigma and abs(value - forecast.value) / sigma > MAX_PLAUSIBLE_Z:
                continue
            return value
    return None


MAX_PLAUSIBLE_Z = 5.0


def _first_mention(text: str, families: set[str]) -> int:
    """Earliest position in the headline of any keyword of these families."""
    lowered = text.casefold()
    positions = [m.start() for family in families for word in FAMILIES.get(family, ())
                 for m in [re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", lowered)] if m]
    return min(positions) if positions else len(lowered)


def match_release(headline: str, published_at: datetime, calendar: Iterable[CalendarRow],
                  sigma: Mapping[tuple[str, str], float], *,
                  received_at: datetime | None = None) -> ReleaseMatch | None:
    """The single release this headline reports, or None when it is not clear which.

    `published_at` decides which releases the article can report: the publisher's own time when
    known, since an article written before a release cannot carry its figure. `received_at` (our
    receipt time, default `published_at`) sets `minutes_since_release`."""
    currencies = headline_currencies(headline)
    families = headline_families(headline)
    if not currencies or not families:
        return None
    numbers = find_numbers(headline)
    candidates: list[tuple[int, int, ReleaseMatch]] = []
    for row in calendar:
        if row.currency not in currencies or row.impact not in {"high", "medium"}:
            continue
        if not (published_at - RELEASE_MATCH_WINDOW <= row.utc <= published_at + PUBLISH_CLOCK_SKEW):
            continue
        row_families = event_families(row.event)
        if (row_families ^ families) & PROVIDER_FAMILIES:
            continue                                   # another survey's figure (see PROVIDER_FAMILIES)
        shared = row_families & families
        forecast = parse_calendar_value(row.forecast)
        if not shared or forecast is None:
            continue
        s = sigma.get((row.currency, row.event))
        actual = _pick_actual(numbers, forecast, s)
        if actual is None:
            continue
        # Base units on both sides: the research store parses "151K" to 151000, as here.
        surprise = actual - forecast.value
        z = (surprise / s) if s else None
        if z is not None and abs(z) > MAX_PLAUSIBLE_Z:
            continue                                   # the number belongs to another indicator
        match = ReleaseMatch(currency=row.currency, event=row.event, release_utc=row.utc, actual=actual,
                             forecast=forecast.value, unit=forecast.unit, surprise=surprise, z=z,
                             minutes_since_release=max(0.0, ((received_at or published_at) - row.utc).total_seconds() / 60))
        candidates.append((_format_score(row.event, headline), _first_mention(headline, shared), match))
    if not candidates:
        return None
    best_format = max(c[0] for c in candidates)
    top = [c for c in candidates if c[0] == best_format]
    earliest = min(c[1] for c in top)
    top = [c for c in top if c[1] == earliest]
    events = {c[2].event for c in top}
    return top[0][2] if len(events) == 1 else None    # two rows still fit equally well: abstain
