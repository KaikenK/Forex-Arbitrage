"""Per-pair reaction to a matched economic release, from the research artifact.

Artifact: `artifacts/release_reaction/reaction_model.json`, written by
`research/run_reaction_eval.py` (walk-forward evaluation in research/artifacts/reaction_eval.json,
results logged in RESEARCH_TRACKER §6, 2026-11-02). What the evaluation found, and therefore
what each output means:

  direction       sign(z) × the indicator's economic sign × the pair side — the theory rule,
                  68% right at 15 min after the release, 75% at 1 min (the learned per-cell β
                  scored lower, so it is not used live).
  p_direction     calibrated P(direction right | |z|) for that rule (ECE ≈ 0.04 out of sample).
  expected_move   expected |move| over the 15 min after the release, and an 80/90% conformal
                  interval (coverage 77% / 87% out of sample).
  priced_in       the move completes within a minute and does not drift afterwards: from 15 min
                  after the release, direction accuracy is 49% (no signal). An article that
                  arrives after the release therefore reports a move that has already happened;
                  post_arrival_move_bps is the typical further |move| over the next 45 min.

Pairs that do not contain the release currency get no direction, only the expected move
(a EUR release moves USD/JPY abnormally ~22% of the time).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .release_extractor import PUBLISH_CLOCK_SKEW, RELEASE_MATCH_WINDOW, CalendarRow, ReleaseMatch, match_release

DEFAULT_ARTIFACT = Path(__file__).resolve().parents[2] / "artifacts" / "release_reaction" / "reaction_model.json"
PRIMARY_TARGET = "r15"


def _keyed(entries: list[dict]) -> dict[tuple, Any]:
    return {tuple(e["key"]) if isinstance(e["key"], list) else e["key"]: e["value"] for e in entries}


def pair_side(pair: str, currency: str) -> int | None:
    base, _, quote = pair.partition("/")
    return 1 if currency == base else -1 if currency == quote else None


# A higher print is bad news for the currency for every unemployment-type release, in every
# country (the economic prior of Andersen, Bollerslev, Diebold & Vega 2003). The exported table lists
# only the US and UK cases the research sample needed, so e.g. a UK or euro-area "Unemployment Rate"
# above forecast was read as good news for the currency.
BAD_WHEN_HIGHER = re.compile(r"unemployment|jobless|claimant count", re.IGNORECASE)


@dataclass
class ReleaseReactionModel:
    sigma: dict[tuple[str, str], float]
    category: dict[tuple[str, str], str]
    negative: set[tuple[str, str]]
    ambiguous: set[tuple[str, str]]
    conf_weights: list[float] | None
    move_cell: dict[tuple, list[float]]
    move_pooled: dict[tuple, list[float]]
    move_pair_mean: dict[str, float]
    split_cell: dict[tuple, list[float]]
    split_pooled: dict[tuple, list[float]]
    split_pair_mean: dict[str, float]
    conformal_q: dict[str, float]
    control_median: dict[str, float]
    post_arrival: dict[str, float]
    fitted_at: str
    sample: dict

    @classmethod
    def load(cls, path: Path = DEFAULT_ARTIFACT) -> ReleaseReactionModel:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if raw.get("schema") != "release_reaction_v1":
            raise ValueError(f"unexpected release-reaction artifact schema {raw.get('schema')!r}")
        target = raw["targets"][PRIMARY_TARGET]
        return cls(
            sigma={(r["currency"], r["event_name"]): float(r["sigma"]) for r in raw["sigma_by_indicator"]
                   if r.get("sigma") and math.isfinite(r["sigma"]) and r["sigma"] > 0},
            category={(r["currency"], r["event_name"]): r["category"] for r in raw["category_by_indicator"]},
            negative={tuple(x) for x in raw["news_sign"]["negative"]},
            ambiguous={tuple(x) for x in raw["news_sign"]["ambiguous"]},
            conf_weights=raw["theory_rule_confidence"][PRIMARY_TARGET]["weights"],
            move_cell=_keyed(target["move_cell"]), move_pooled=_keyed(target["move_pooled"]),
            move_pair_mean=target["move_pair_mean"],
            split_cell=_keyed(target["split_move_cell"]), split_pooled=_keyed(target["split_move_pooled"]),
            split_pair_mean=target["split_pair_mean"],
            conformal_q=target["conformal_q"],
            control_median={p: v["v_release"] for p, v in raw["control_median_bps"].items()},
            post_arrival=raw["post_arrival_mean_abs_bps"],
            fitted_at=raw["fitted_at"], sample=raw["sample"],
        )

    # ------------------------------------------------------------------ components
    def news_sign(self, currency: str, event: str) -> int | None:
        if (currency, event) in self.ambiguous:
            return None
        if (currency, event) in self.negative or BAD_WHEN_HIGHER.search(event):
            return -1
        return 1

    def p_direction(self, z: float) -> float | None:
        """Fitted on |z| and log(1+|z|), the logistic peaks at |z|* = -w2/w1 - 1 (about 2.3) and
        then falls. It is read at min(|z|, |z|*), so a larger surprise is never less certain."""
        if self.conf_weights is None:
            return None
        a = abs(z)
        w = self.conf_weights
        if w[1] < 0 < w[2]:
            a = min(a, max(0.0, -w[2] / w[1] - 1.0))
        x = w[0] + w[1] * a + w[2] * math.log1p(a)
        return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))

    @staticmethod
    def _move(cells, pooled, means, currency, category, pair, abs_z, v) -> float | None:
        coef = cells.get((currency, category, pair)) if category else None
        if coef is None:
            coef = pooled.get((currency, pair))
        if coef is not None and v is not None:
            return max(0.0, coef[0] + coef[1] * abs_z + coef[2] * v)
        return means.get(pair)

    # ------------------------------------------------------------------ scoring
    def score(self, match: ReleaseMatch, pairs: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        category = self.category.get((match.currency, match.event))
        sign = self.news_sign(match.currency, match.event)
        for pair in pairs:
            v = self.control_median.get(pair)
            entry: dict[str, Any] = {
                "basis": "release", "release_event": match.event, "release_currency": match.currency,
                "release_utc": match.release_utc.isoformat(), "actual": match.actual,
                "forecast": match.forecast, "standardized_surprise": match.z,
                "minutes_since_release": round(match.minutes_since_release, 2),
                "priced_in": match.minutes_since_release >= 1.0,
                "post_arrival_move_bps": self.post_arrival.get(pair),
                "direction": 0, "p_direction": None, "expected_move_bps": None, "move_interval_bps": None,
                "horizon": {"reaction": "complete within 1 min of the release, persists to 120 min",
                            "post_arrival_direction": "none detected (49% from release + 15 min)"},
            }
            if match.z is None:
                out[pair] = entry
                continue
            abs_z = abs(match.z)
            entry["expected_move_bps"] = self._move(self.move_cell, self.move_pooled, self.move_pair_mean,
                                                    match.currency, category, pair, abs_z, v)
            side = pair_side(pair, match.currency)
            if side is not None and sign is not None and match.z != 0:
                entry["direction"] = int(math.copysign(1, match.z) * sign * side)
                entry["p_direction"] = self.p_direction(match.z)
                centre = self._move(self.split_cell, self.split_pooled, self.split_pair_mean,
                                    match.currency, category, pair, abs_z, v)
                if centre is not None:
                    entry["move_interval_bps"] = {
                        level: [max(0.0, centre - q), centre + q] for level, q in self.conformal_q.items()}
            out[pair] = entry
        return out


# Official sources name their own economy implicitly ("Consumer Price Index – September").
SOURCE_CURRENCY = {
    "bls.gov": "U.S.", "bea.gov": "U.S.", "census.gov": "U.S.", "federalreserve.gov": "U.S.",
    "ecb.europa.eu": "Euro area", "ec.europa.eu": "Euro area", "bankofengland.co.uk": "UK",
    "ons.gov.uk": "UK", "boj.or.jp": "Japan", "snb.ch": "Swiss", "rbi.org.in": "India",
}


def source_currency_prefix(source_url: str) -> str:
    from urllib.parse import urlparse

    host = urlparse(source_url or "").netloc.casefold()
    for domain, words in SOURCE_CURRENCY.items():
        if host == domain or host.endswith("." + domain):
            return f"{words} "
    return ""


class ReleaseAugmenter:
    """Live glue: headline + publication time → matched release → per-pair release scores.

    `calendar` is the service's EconomicCalendar (cached ForexFactory week); only its
    `releases_between` is used, so a stub works in tests.
    """

    def __init__(self, calendar: Any, model: ReleaseReactionModel) -> None:
        self._calendar = calendar
        self._model = model

    def match(self, headline: str, published_at: datetime | None, *, body: str = "",
              source_url: str = "", publisher_time: datetime | None = None) -> ReleaseMatch | None:
        """Headline first; if that fails, headline + summary (agency feeds put the figures in
        the summary). An agency or central-bank source names its own currency.

        `published_at` is our receipt time. The candidate releases are anchored on the feed's
        `publisher_time` when it is known and not ahead of receipt: feeds can reach us up to an
        hour late (CDN caches), and a preview written before a release must not match it."""
        if not headline or published_at is None:
            return None
        received = published_at.astimezone(UTC)
        published = received
        if publisher_time is not None and publisher_time.astimezone(UTC) <= received + PUBLISH_CLOCK_SKEW:
            published = publisher_time.astimezone(UTC)
        start = (published - RELEASE_MATCH_WINDOW).timestamp()
        end = (published + PUBLISH_CLOCK_SKEW).timestamp()
        rows = [CalendarRow(currency=r.currency, event=r.event, utc=datetime.fromtimestamp(r.timestamp, UTC),
                            forecast=r.forecast, impact=r.impact)
                for r in self._calendar.releases_between(start, end)]
        if not rows:
            return None
        prefix = source_currency_prefix(source_url)
        texts = [f"{prefix}{headline}"]
        if body and body.strip() and body.strip() != headline.strip():
            texts.append(f"{prefix}{headline}. {body[:600]}")
        for text in texts:
            match = match_release(text, published, rows, self._model.sigma, received_at=received)
            if match is not None:
                return match
        return None

    def score(self, headline: str, published_at: datetime | None, pairs: list[str], *, body: str = "",
              source_url: str = "", publisher_time: datetime | None = None) -> dict[str, dict[str, Any]]:
        match = self.match(headline, published_at, body=body, source_url=source_url,
                           publisher_time=publisher_time)
        return {} if match is None else self._model.score(match, pairs)
