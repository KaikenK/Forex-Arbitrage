"""Live per-currency market bias for /v1/currency-index (behind SEMANTIC_CURRENCY_BIAS).

The formula is `currency_bias_core.py`, a byte-identical copy of the research module that
the pre-registered H6 test uses (RESEARCH_TRACKER §6, 2026-10-25), so the service and the
paper compute the same signal. This class only keeps the rolling state the formula needs:

  per currency  the scored headlines of the last 7 days (24 h enter the signal, 7 days are
                the novelty context), their embeddings, and the history of the currency's
                own B values (for the strength z-score);
  per pair      the reaction model's recent magnitudes (for the separate event-risk block).

Every analysed article is observed once, after the analysis, with the sentiment score the
analyzer already computed (no second FinBERT pass).

Provisional live settings. H6 leaves some clauses open until a §6 decision; the service
must still answer, so it uses the values in PROVISIONAL_SETTINGS and reports them in every
payload under "provisional_settings". They are engineering defaults for the live demo, not
research decisions, and the H6 scripts refuse to run on them.

Live input gaps (reported in every payload, never faked):
  - No consensus forecasts reach the service, so the event component E is unavailable
    unless a caller sends `metadata.release_currency` + `metadata.oriented_surprise`
    (sign-oriented, Option A sigma). Without it the currency bias is the news component alone.
  - Release proximity comes from the live economic calendar (`economic_calendar.py`) when
    one is attached: `release_proximity` is non-null only while a high-impact, time-known
    release of either pair currency is within the risk window. That calendar has forecasts
    but no actuals, so it cannot supply the event component either. Without a calendar the
    event-risk block reports `release_proximity: null` and `calendar: "unavailable"`.
  - Novelty uses the service's embedder: the sentence-transformer when enabled (as in the
    research), otherwise the hashing embedder, which is a weaker similarity measure.
  - The horizon is "none established" until run_h6's horizon-output file exists.
"""
from __future__ import annotations

import json
import math
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import currency_bias_core as core

NOVELTY_LOOKBACK_HOURS = 168.0          # semantic/novelty.py: 7 days, top 5, min context 3
NOVELTY_TOP_K = 5
NOVELTY_MIN_CONTEXT = 3
MAX_HISTORY_VALUES = 5000
RISK_LOOKBACK_MINUTES = 60.0
LIVE_PAIRS: tuple[str, ...] = ("EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/INR")

PROVISIONAL_SETTINGS: dict[str, object] = {
    "novelty_missing": "as_novel",
    "relevance_unmatched": "indirect",
    "event_window_hours": 24.0,
    "no_data_value": "raw_zero",
    "zscore_scheme": "expanding_causal",
    "zscore_min_history": 30,
    "label_threshold_z": 0.5,
    "silent_currency": "strength 0 while a currency's B history is constant and it has no news",
    "pair_confidence": "bias-weighted mean of the two currencies' confidences",
}

LIVE_INPUT_GAPS: tuple[str, ...] = (
    "event component: no consensus-vs-actual surprise reaches the service (the calendar feed has "
    "forecasts but no actuals); E is used only when a caller sends metadata.release_currency and "
    "metadata.oriented_surprise",
    "event risk: no live economic calendar, so release proximity is unknown (null)",
    "novelty: service embedder (hashing unless the sentence-transformer is enabled)",
)
CALENDAR_GAP = "event risk: release proximity from the ForexFactory weekly calendar (current week only)"


@dataclass
class _CurrencyState:
    times: deque = field(default_factory=deque)
    polarities: deque = field(default_factory=deque)
    confidences: deque = field(default_factory=deque)
    novelties: deque = field(default_factory=deque)
    relevances: deque = field(default_factory=deque)
    vectors: deque = field(default_factory=deque)
    headlines: deque = field(default_factory=deque)
    release_times: deque = field(default_factory=deque)
    release_values: deque = field(default_factory=deque)
    b_history: deque = field(default_factory=lambda: deque(maxlen=MAX_HISTORY_VALUES))


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector] if norm > 0 else list(vector)


def rolling_novelty(prior: list[list[float]], vector: list[float], *,
                    top_k: int = NOVELTY_TOP_K, min_context: int = NOVELTY_MIN_CONTEXT) -> float | None:
    """1 - mean similarity to the top_k closest strictly-prior stories (semantic/novelty.py)."""
    if len(prior) < min_context:
        return None
    sims = sorted((sum(a * b for a, b in zip(p, vector)) for p in prior), reverse=True)
    top = sims[: min(top_k, len(sims))]
    return 1.0 - sum(top) / len(top)


class CurrencyBiasTracker:
    def __init__(
        self,
        embed: Callable[[str], list[float]],
        *,
        horizon_file: Path | None = None,
        clock: Callable[[], datetime] | None = None,
        settings: dict[str, object] | None = None,
        embedder_name: str = "hashing",
        calendar=None,
    ) -> None:
        self._embed = embed
        self._calendar = calendar
        self._horizon_file = horizon_file
        self._clock = clock or (lambda: datetime.now(UTC))
        self._settings = {**PROVISIONAL_SETTINGS, **(settings or {})}
        self._embedder_name = embedder_name
        self._lock = threading.Lock()
        self._currencies = {c: _CurrencyState() for c in core.CURRENCIES}
        self._pair_risk: dict[str, deque] = {p: deque() for p in LIVE_PAIRS}

    # ------------------------------------------------------------ ingest
    def observe(self, *, headline: str, published_at: datetime | None, polarity: float, confidence: float,
                pair_scores: dict[str, dict[str, float]] | None = None,
                metadata: dict[str, str] | None = None) -> None:
        stamp = (published_at or self._clock())
        stamp = stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)
        t = stamp.timestamp()
        text = str(headline or "")
        codes = [c for c in core.tag_currencies(text) if c in self._currencies]
        vector = _normalize(list(self._embed(text))) if codes and text else None
        with self._lock:
            for code in codes:
                state = self._currencies[code]
                self._expire(state, t)
                prior = [v for ts, v in zip(state.times, state.vectors)
                         if t - NOVELTY_LOOKBACK_HOURS * 3600 <= ts < t]
                state.times.append(t)
                state.polarities.append(float(polarity))
                state.confidences.append(float(confidence))
                state.novelties.append(rolling_novelty(prior, vector))
                state.relevances.append(core.relevance(text, code))
                state.vectors.append(vector)
                state.headlines.append(text)
                self._resort(state)
            self._observe_release(metadata or {}, t)
            now = self._clock().timestamp()
            for pair, score in (pair_scores or {}).items():
                if pair in self._pair_risk:
                    self._pair_risk[pair].append((t, float(score.get("magnitude", 0.0)), float(score.get("confidence", 0.0))))
            for risk in self._pair_risk.values():
                while risk and risk[0][0] < now - RISK_LOOKBACK_MINUTES * 60:
                    risk.popleft()
            for state in self._currencies.values():
                while state.release_times and state.release_times[0] < now - NOVELTY_LOOKBACK_HOURS * 3600:
                    state.release_times.popleft()
                    state.release_values.popleft()
            for state in self._currencies.values():
                value = self._news(state, now).value
                state.b_history.append(0.0 if value is None and self._settings["no_data_value"] == "raw_zero" else value)

    def _observe_release(self, metadata: dict[str, str], t: float) -> None:
        currency = str(metadata.get("release_currency") or "").upper()
        if currency not in self._currencies or metadata.get("oriented_surprise") in (None, ""):
            return
        try:
            value = max(-5.0, min(5.0, float(metadata["oriented_surprise"])))
        except (TypeError, ValueError):
            return
        state = self._currencies[currency]
        state.release_times.append(t)
        state.release_values.append(value)

    def _expire(self, state: _CurrencyState, t: float) -> None:
        cutoff = t - NOVELTY_LOOKBACK_HOURS * 3600
        while state.times and state.times[0] < cutoff:
            for name in ("times", "polarities", "confidences", "novelties", "relevances", "vectors", "headlines"):
                getattr(state, name).popleft()

    @staticmethod
    def _resort(state: _CurrencyState) -> None:
        """Keep arrays time-sorted (feeds can deliver out of order)."""
        if len(state.times) < 2 or state.times[-2] <= state.times[-1]:
            return
        rows = sorted(zip(state.times, state.polarities, state.confidences, state.novelties,
                          state.relevances, state.vectors, state.headlines), key=lambda r: r[0])
        for index, name in enumerate(("times", "polarities", "confidences", "novelties", "relevances", "vectors", "headlines")):
            setattr(state, name, deque(r[index] for r in rows))

    # ------------------------------------------------------------ snapshot
    def _news(self, state: _CurrencyState, as_of: float) -> core.NewsComponent:
        return core.news_component(
            list(state.times), list(state.polarities), list(state.confidences), list(state.novelties),
            list(state.relevances), as_of, novelty_missing=str(self._settings["novelty_missing"]),
            relevance_unmatched=str(self._settings["relevance_unmatched"]))

    def _strength(self, value: float | None, history: deque) -> float | None:
        values = [v for v in history if v is not None]
        if len(values) < int(self._settings["zscore_min_history"]):
            return None
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        raw = 0.0 if value is None and self._settings["no_data_value"] == "raw_zero" else value
        if var <= 0.0 and raw is not None and raw == mean:
            return 0.0      # silent currency: constant history and no news now -> neutral
        return core.standardize(raw, mean, math.sqrt(var))

    def horizon(self) -> dict[str, object]:
        if self._horizon_file is not None and self._horizon_file.exists():
            try:
                data = json.loads(self._horizon_file.read_text(encoding="utf-8"))
                return {"horizon_minutes": data.get("horizon_minutes"), "label": data.get("label"),
                        "source": str(self._horizon_file.name)}
            except (OSError, json.JSONDecodeError):
                pass
        return {"horizon_minutes": None, "label": "none established", "source": None}

    def snapshot(self, query: dict[str, str] | None = None) -> dict[str, object]:
        now = self._clock()
        as_of = now.timestamp()
        threshold = float(self._settings["label_threshold_z"])
        currencies: dict[str, dict[str, object]] = {}
        with self._lock:
            for code, state in self._currencies.items():
                comp = self._news(state, as_of)
                news_z = self._strength(comp.value, state.b_history)
                event = core.event_component(list(state.release_times), list(state.release_values), as_of,
                                             window_hours=float(self._settings["event_window_hours"]))
                # E's own standardization would need a live surprise history; with the live
                # gap above it is reported raw and not added, so bias = news z alone.
                bias = news_z
                currencies[code] = {
                    "bias": bias, "strength": bias, "label": core.bias_label(bias, threshold=threshold),
                    "confidence": round(core.signal_confidence(comp), 4),
                    "news_component": comp.value, "event_component_raw": event,
                    "event_component_used": False,
                    "headline_count": comp.n_headlines, "effective_n": round(comp.effective_n, 3),
                    "agreement": round(comp.agreement, 4),
                    "newest_age_hours": comp.newest_age_hours,
                    "status": "warming_up" if news_z is None and comp.value is not None else
                              ("no_news" if comp.value is None else "live"),
                    "top_headlines": [list(state.headlines)[i] for i in comp.contributors],
                }
            pairs = {pair: self._pair(pair, currencies, as_of, threshold) for pair in LIVE_PAIRS}
        gaps = list(LIVE_INPUT_GAPS)
        if self._calendar is not None:
            gaps[1] = CALENDAR_GAP
        return {
            "as_of": now.isoformat(), "enabled": True,
            "method": "H6 locked formula (currency_bias_core), live state",
            "currencies": currencies, "pairs": pairs, "market_component": None,
            "horizon": self.horizon(), "provisional_settings": dict(self._settings),
            "embedder": self._embedder_name, "live_input_gaps": gaps,
            "calendar": None if self._calendar is None else self._calendar.status(),
            "query": dict(query or {}),
        }

    def hooks(self) -> tuple[Callable, Callable]:
        """(sentiment_tap, analysis_observer) for build_runtime_service."""
        from collections import OrderedDict

        last: OrderedDict[str, tuple[float, float]] = OrderedDict()

        def tap(event_id: str, inference) -> None:
            last[event_id] = (float(inference.score), float(inference.confidence))
            while len(last) > 256:
                last.popitem(last=False)

        def observer(article, analysis) -> None:
            polarity, confidence = last.pop(analysis.event_id, (analysis.sentiment_score, analysis.confidence))
            self.observe(headline=article.headline or article.article_text, published_at=article.published_at,
                         polarity=polarity, confidence=confidence, pair_scores=analysis.pair_scores,
                         metadata=article.metadata)

        return tap, observer

    def _pair(self, pair: str, currencies: dict, as_of: float, threshold: float) -> dict[str, object]:
        base, _, quote = pair.partition("/")
        b, q = currencies[base], currencies[quote]
        relative = core.pair_bias(b["bias"], q["bias"])
        weights = [abs(b["bias"] or 0.0), abs(q["bias"] or 0.0)]
        confidence = ((weights[0] * b["confidence"] + weights[1] * q["confidence"]) / sum(weights)
                      if sum(weights) > 0 else 0.0)
        recent = [(t, m, c) for t, m, c in self._pair_risk[pair] if as_of - t <= RISK_LOOKBACK_MINUTES * 60]
        expected_bps = max((abs(m) * 1e4 * core.decay((as_of - t) / 3600.0) for t, m, _ in recent), default=0.0)
        event_risk: dict[str, object] = {"expected_move_bps": round(expected_bps, 3), "recent_scored_items": len(recent),
                                         "lookback_minutes": RISK_LOOKBACK_MINUTES, "release_proximity": None,
                                         "calendar": "unavailable"}
        if self._calendar is not None:
            proximity = self._calendar.proximity((base, quote))
            # Non-null only inside the risk window; the backend adds its release penalty
            # whenever this is non-null, so a release days away must not set it.
            event_risk["release_proximity"] = proximity["in_window"]
            event_risk["next_release"] = {"minutes": proximity["minutes_to_next"], "event": proximity["next_event"]}
            event_risk["last_release"] = {"minutes_ago": proximity["minutes_since_last"], "event": proximity["last_event"]}
            event_risk["release_window_minutes"] = proximity["window_minutes"]
            event_risk["calendar"] = self._calendar.status()["state"]
        return {
            "relative_bias": relative, "label": core.bias_label(relative, threshold=threshold),
            "confidence": round(confidence, 4),
            "components": {"base": base, "base_bias": b["bias"], "quote": quote, "quote_bias": q["bias"]},
            "event_risk": event_risk,
        }
