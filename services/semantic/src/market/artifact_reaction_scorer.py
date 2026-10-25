from __future__ import annotations

from collections.abc import Callable
from datetime import UTC
from datetime import datetime
import hashlib
import math
from pathlib import Path

from retrieval.text_embedder import TextEmbedder

from .interfaces import EventReactionScorer
from .models import MarketReactionSignal
from .pair_assets import scoped_text_token
from .reaction_artifact import RuntimeReactionArtifact
from .reaction_artifact import RuntimeReactionRow
from .reaction_artifact import load_reaction_artifact
from .schemas import AnalysisContext

DEFAULT_FALLBACK_HORIZON = 60
CALIBRATION_EPSILON = 1e-9


class ArtifactReactionScorer(EventReactionScorer):
    def __init__(
        self,
        artifact: RuntimeReactionArtifact,
        preferred_horizon_minutes: int | None = None,
        clock: Callable[[], datetime] | None = None,
        embedder: TextEmbedder | None = None,
    ) -> None:
        """Initializes an artifact-backed market reaction scorer."""
        self._artifact = artifact
        self._preferred_horizon_minutes = preferred_horizon_minutes
        self._clock = clock or _utc_now
        self._embedder = embedder

    @classmethod
    def from_path(
        cls,
        artifact_path: Path,
        preferred_horizon_minutes: int | None = None,
        clock: Callable[[], datetime] | None = None,
        embedder: TextEmbedder | None = None,
    ) -> ArtifactReactionScorer:
        """Builds an artifact-backed reaction scorer from a saved JSON artifact."""
        artifact = load_reaction_artifact(artifact_path)
        return cls(
            artifact=artifact,
            preferred_horizon_minutes=preferred_horizon_minutes,
            clock=clock,
            embedder=embedder,
        )

    @property
    def embedding_dimensions(self) -> int:
        """Returns the hash embedding dimension used by the loaded artifact."""
        return self._artifact.hash_dimension

    @property
    def model_descriptor(self) -> str:
        """Human-readable description of the model actually loaded.

        Read from the artifact rather than hardcoded: the methodology panel used to
        claim "k-NN (k=5, cosine similarity)" for every analysis, which stopped
        being true the moment a linear artifact was trained and served.
        """
        family = (self._artifact.model_family or "unknown").lower()
        feature_note = (
            f"{self._artifact.hash_dimension}-dim semantic embeddings"
            if self._artifact.uses_semantic_text_features
            else f"{self._artifact.hash_dimension}-dim hashed text + structured features"
        )
        if family == "knn":
            return f"k-NN (k={self._artifact.k_neighbors}, cosine similarity) over {feature_note}"
        if family == "linear":
            return f"Linear direction/magnitude/horizon heads over {feature_note}"
        return f"{family} model over {feature_note}"

    async def score(self, context: AnalysisContext) -> list[MarketReactionSignal]:
        """Scores one analysis context using the loaded reaction artifact."""
        pairs = context.event.symbols or []
        if not pairs:
            return []

        predicted_signals: list[MarketReactionSignal] = []
        for pair in pairs:
            horizon_minutes = self._resolve_horizon(context, pair)
            query_row = self._query_row(context, pair, horizon_minutes)
            probabilities = _orient_to_pair(
                self._direction_probabilities(query_row),
                pair=pair,
                orientation=self._artifact.direction_orientation,
            )
            predicted_direction = self._probability_to_direction(probabilities)
            predicted_signals.append(
                MarketReactionSignal(
                    event_id=context.event.event_id,
                    symbol=pair,
                    direction=float(predicted_direction),
                    magnitude=self._predicted_magnitude(query_row),
                    confidence=max(probabilities),
                    horizon_minutes=horizon_minutes,
                    key_drivers=self._key_drivers(query_row),
                    produced_at=self._clock(),
                ),
            )
        return predicted_signals

    def _query_row(self, context: AnalysisContext, pair: str, horizon_minutes: int) -> RuntimeReactionRow:
        article_text = _article_text(context)
        metadata = self._metadata(context)
        feature_vector, horizon_feature_vector = self._feature_vectors(
            context=context,
            article_text=article_text,
            pair=pair,
            horizon_minutes=horizon_minutes,
            metadata=metadata,
        )
        return RuntimeReactionRow(
            event_id=context.event.event_id,
            pair=pair,
            horizon_minutes=horizon_minutes,
            timestamp=context.event.published_at,
            direction_label=0,
            magnitude_label=0.0,
            feature_vector=feature_vector,
            horizon_feature_vector=horizon_feature_vector,
            source_metadata=metadata,
            event_category=context.article.event_category,
            macro_event_type=context.article.macro_event_type,
            scheduled=context.article.scheduled,
            actual=context.article.actual,
            forecast=context.article.forecast,
            previous=context.article.previous,
            standardized_surprise=context.article.standardized_surprise,
            calendar_source=context.article.calendar_source,
            source_url=context.article.source_url,
        )

    def _metadata(self, context: AnalysisContext) -> dict[str, str]:
        metadata = {str(key): str(value) for key, value in context.article.metadata.items()}
        metadata["source"] = context.event.source.value
        if context.article.event_category is not None:
            metadata["event_category"] = context.article.event_category
        if context.article.macro_event_type is not None:
            metadata["macro_event_type"] = context.article.macro_event_type
        if context.article.scheduled is not None:
            metadata["scheduled"] = "true" if context.article.scheduled else "false"
        if context.article.actual is not None:
            metadata["actual"] = str(context.article.actual)
        if context.article.forecast is not None:
            metadata["forecast"] = str(context.article.forecast)
        if context.article.previous is not None:
            metadata["previous"] = str(context.article.previous)
        if context.article.standardized_surprise is not None:
            metadata["standardized_surprise"] = str(context.article.standardized_surprise)
        if context.article.calendar_source is not None:
            metadata["calendar_source"] = context.article.calendar_source
        if context.article.source_url is not None:
            metadata["source_url"] = context.article.source_url
        return metadata

    def _feature_vectors(
        self,
        *,
        context: AnalysisContext,
        article_text: str,
        pair: str,
        horizon_minutes: int,
        metadata: dict[str, str],
    ) -> tuple[list[float], list[float]]:
        if (
            self._artifact.uses_semantic_text_features
            and self._embedder is not None
            and self._embedder.dimensions == self._artifact.hash_dimension
        ):
            return (
                self._semantic_feature_vector(
                    article_text=article_text,
                    pair=pair,
                    horizon_minutes=horizon_minutes,
                    metadata=metadata,
                    context=context,
                    include_horizon=True,
                ),
                self._semantic_feature_vector(
                    article_text=article_text,
                    pair=pair,
                    horizon_minutes=horizon_minutes,
                    metadata=metadata,
                    context=context,
                    include_horizon=False,
                ),
            )

        return (
            self._hashed_feature_vector(
                context=context,
                article_text=article_text,
                pair=pair,
                horizon_minutes=horizon_minutes,
                metadata=metadata,
                include_horizon=True,
            ),
            self._hashed_feature_vector(
                context=context,
                article_text=article_text,
                pair=pair,
                horizon_minutes=horizon_minutes,
                metadata=metadata,
                include_horizon=False,
            ),
        )

    def _hashed_feature_vector(
        self,
        *,
        context: AnalysisContext,
        article_text: str,
        pair: str,
        horizon_minutes: int,
        metadata: dict[str, str],
        include_horizon: bool,
    ) -> list[float]:
        values = [0.0 for _ in range(self._artifact.hash_dimension)]
        for token in _text_tokens(article_text):
            _update_feature(
                values,
                scoped_text_token(token, pair, self._artifact.text_token_scope),
                self._artifact.hash_dimension,
                1.0,
            )
        _update_feature(values, f"pair={pair}", self._artifact.hash_dimension, 1.0)
        if include_horizon:
            _update_feature(values, f"horizon={horizon_minutes}", self._artifact.hash_dimension, 1.0)

        event_family = _event_family_from_metadata(metadata)
        if event_family is not None:
            _update_feature(values, f"event_family={event_family}", self._artifact.hash_dimension, 1.0)
        _update_feature(
            values,
            f"scheduled={metadata.get('scheduled', 'false').strip().casefold()}",
            self._artifact.hash_dimension,
            1.0,
        )
        resolved_surprise = _resolved_surprise_value(
            standardized_surprise=context.article.standardized_surprise,
            actual=context.article.actual,
            forecast=context.article.forecast,
            previous=context.article.previous,
        )
        if resolved_surprise is not None:
            _update_feature(
                values,
                f"surprise_bucket={_surprise_bucket(resolved_surprise)}",
                self._artifact.hash_dimension,
                1.0,
            )

        for metadata_field in self._artifact.metadata_fields:
            metadata_value = metadata.get(metadata_field)
            if metadata_value is None or not metadata_value.strip():
                continue
            _update_feature(
                values,
                f"{metadata_field}={metadata_value.strip().casefold()}",
                self._artifact.hash_dimension,
                1.0,
            )

        return _normalize_vector(
            values
            + _structured_feature_values(
                horizon_minutes=horizon_minutes,
                scheduled=context.article.scheduled,
                actual=context.article.actual,
                forecast=context.article.forecast,
                previous=context.article.previous,
                standardized_surprise=context.article.standardized_surprise,
                include_horizon=include_horizon,
            ),
        )

    def _semantic_feature_vector(
        self,
        *,
        article_text: str,
        pair: str,
        horizon_minutes: int,
        metadata: dict[str, str],
        context: AnalysisContext,
        include_horizon: bool,
    ) -> list[float]:
        """Produces a dense semantic embedding via the injected embedder."""
        semantic_text = _semantic_text(
            article_text=article_text,
            pair=pair,
            horizon_minutes=horizon_minutes,
            metadata=metadata,
            context=context,
            include_horizon=include_horizon,
        )
        return _normalize_vector(
            [float(value) for value in self._embedder.embed(semantic_text)]
            + _structured_feature_values(
                horizon_minutes=horizon_minutes,
                scheduled=context.article.scheduled,
                actual=context.article.actual,
                forecast=context.article.forecast,
                previous=context.article.previous,
                standardized_surprise=context.article.standardized_surprise,
                include_horizon=include_horizon,
            ),
        )

    def _resolve_horizon(self, context: AnalysisContext, pair: str) -> int:
        available_horizons = self._artifact.available_horizons()
        if not available_horizons:
            return DEFAULT_FALLBACK_HORIZON
        if self._preferred_horizon_minutes in available_horizons:
            return int(self._preferred_horizon_minutes)
        if _has_horizon_model(self._artifact):
            metadata = self._metadata(context)
            article_text = _article_text(context)
            _, horizon_feature_vector = self._feature_vectors(
                context=context,
                article_text=article_text,
                pair=pair,
                horizon_minutes=DEFAULT_FALLBACK_HORIZON,
                metadata=metadata,
            )
            predicted_horizon = _predicted_horizon(self._artifact, horizon_feature_vector)
            if predicted_horizon in available_horizons:
                return predicted_horizon
            return min(available_horizons, key=lambda horizon: abs(horizon - predicted_horizon))

        horizon_counts: dict[int, int] = {horizon_minutes: 0 for horizon_minutes in available_horizons}
        # Read counts from the artifact rather than iterating its rows: a weights-only
        # linear artifact ships no rows, but records how often each horizon occurred.
        for horizon_minutes, count in self._artifact.horizon_counts().items():
            horizon_counts[horizon_minutes] = count
        return max(horizon_counts, key=lambda key: (horizon_counts[key], -abs(key - DEFAULT_FALLBACK_HORIZON)))

    def _nearest_neighbors(self, row: RuntimeReactionRow) -> list[tuple[RuntimeReactionRow, float]]:
        selected_neighbors: list[tuple[RuntimeReactionRow, float]] = []
        seen_event_ids: set[str] = set()
        for predicate in _neighbor_tiers(row):
            ranked_rows = sorted(
                (
                    (training_row, _cosine_similarity(training_row.feature_vector, row.feature_vector))
                    for training_row in self._artifact.training_rows
                    if predicate(training_row) and training_row.event_id not in seen_event_ids
                ),
                key=lambda item: item[1],
                reverse=True,
            )
            for training_row, similarity in ranked_rows:
                selected_neighbors.append((training_row, similarity))
                seen_event_ids.add(training_row.event_id)
                if len(selected_neighbors) >= self._artifact.k_neighbors:
                    return selected_neighbors
        return selected_neighbors

    def _direction_probabilities(self, row: RuntimeReactionRow) -> list[float]:
        probabilities = self._uncalibrated_direction_probabilities(row)
        return _apply_direction_calibration(probabilities, self._artifact.direction_calibration_temperature)

    def _uncalibrated_direction_probabilities(self, row: RuntimeReactionRow) -> list[float]:
        if self._has_linear_model():
            return self._linear_direction_probabilities(row)

        neighbors = self._nearest_neighbors(row)
        if not neighbors:
            return [0.0, 1.0, 0.0]

        weights = [0.0, 0.0, 0.0]
        for neighbor, similarity in neighbors:
            direction_index = _direction_to_index(neighbor.direction_label)
            weights[direction_index] += self._neighbor_weight(neighbor, row, similarity)

        total_weight = sum(weights)
        if total_weight == 0:
            return [0.0, 1.0, 0.0]
        return [weight / total_weight for weight in weights]

    def _predicted_magnitude(self, row: RuntimeReactionRow) -> float:
        if self._has_linear_model():
            return self._linear_predicted_magnitude(row)

        neighbors = self._nearest_neighbors(row)
        if not neighbors:
            return 0.0
        total_weight = sum(self._neighbor_weight(neighbor, row, similarity) for neighbor, similarity in neighbors)
        if total_weight == 0:
            return sum(neighbor.magnitude_label for neighbor, _ in neighbors) / len(neighbors)
        weighted_sum = sum(
            neighbor.magnitude_label * self._neighbor_weight(neighbor, row, similarity)
            for neighbor, similarity in neighbors
        )
        return weighted_sum / total_weight

    def _neighbor_weight(
        self,
        neighbor: RuntimeReactionRow,
        query_row: RuntimeReactionRow,
        similarity: float,
    ) -> float:
        if similarity <= 0:
            return 0.0
        reliability_weight = _neighbor_reliability_weight(self._artifact, neighbor)
        recency_weight = _recency_weight(
            query_timestamp=query_row.timestamp,
            neighbor_timestamp=neighbor.timestamp,
            half_life_days=self._artifact.recency_half_life_days,
        )
        return similarity * reliability_weight * recency_weight

    def _probability_to_direction(self, probabilities: list[float]) -> int:
        max_index = max(range(len(probabilities)), key=lambda index: probabilities[index])
        if max_index == 0:
            return -1
        if max_index == 1:
            return 0
        return 1

    def _key_drivers(self, row: RuntimeReactionRow) -> list[str]:
        key_drivers = [row.pair, f"horizon:{row.horizon_minutes}m"]
        source_value = row.source_metadata.get("source")
        if source_value is not None:
            key_drivers.append(f"source:{source_value}")
        event_family = _event_family(row)
        if event_family is not None:
            key_drivers.append(f"event:{event_family}")
        if row.scheduled is not None:
            key_drivers.append("scheduled" if row.scheduled else "unscheduled")
        if self._artifact.direction_calibration_temperature is not None:
            key_drivers.append("confidence:calibrated")
        if self._artifact.direction_orientation == "usd":
            key_drivers.append("orientation:usd" if _usd_side(row.pair) else "orientation:no-usd-leg")
        return key_drivers

    def _has_linear_model(self) -> bool:
        return (
            self._artifact.model_family == "linear"
            and self._artifact.direction_classes is not None
            and self._artifact.direction_intercepts is not None
            and self._artifact.direction_coefficients is not None
            and self._artifact.magnitude_intercept is not None
            and self._artifact.magnitude_coefficients is not None
        )

    def _linear_direction_probabilities(self, row: RuntimeReactionRow) -> list[float]:
        logits: list[float] = []
        for intercept, weights in zip(
            self._artifact.direction_intercepts or [],
            self._artifact.direction_coefficients or [],
        ):
            logits.append(float(intercept + sum(weight * value for weight, value in zip(weights, row.feature_vector))))
        if not logits:
            return [0.0, 1.0, 0.0]

        probabilities = _softmax(logits)
        probabilities = [0.0, 0.0, 0.0]
        for direction_class, class_probability in zip(self._artifact.direction_classes or [], _softmax(logits)):
            probabilities[_direction_to_index(direction_class)] = class_probability
        return probabilities

    def _linear_predicted_magnitude(self, row: RuntimeReactionRow) -> float:
        if self._artifact.magnitude_intercept is None or self._artifact.magnitude_coefficients is None:
            return 0.0
        return float(
            self._artifact.magnitude_intercept
            + sum(
                weight * value
                for weight, value in zip(self._artifact.magnitude_coefficients, row.feature_vector)
            )
        )


def _article_text(context: AnalysisContext) -> str:
    """Headline plus body, without repeating the headline.

    The Arbex bridge sends a feed headline as both `headline` and `article_text`, so the
    body equals the headline and every token used to be counted twice — unlike the training
    rows, whose text holds each sentence once.
    """
    headline = context.event.headline or ""
    body = context.event.body or ""
    if body.strip() == headline.strip():
        body = ""
    return "\n".join(filter(None, [headline, body]))


def _usd_side(pair: str) -> int:
    """+1 if USD is the base (the pair rises when USD strengthens), -1 if quote, 0 if absent."""
    base, _, quote = pair.strip().upper().partition("/")
    if base == "USD":
        return 1
    if quote == "USD":
        return -1
    return 0


def _orient_to_pair(probabilities: list[float], *, pair: str, orientation: str) -> list[float]:
    """Turn [down, neutral, up] probabilities for the model's label into the pair's own.

    A "usd" artifact predicts the dollar's move. For EUR/USD a stronger dollar means the
    pair falls, so down and up swap. A pair with no USD leg gets no direction at all: the
    model says nothing about it, and a neutral answer is better than an invented one.
    """
    if orientation != "usd":
        return probabilities
    side = _usd_side(pair)
    if side == 1:
        return probabilities
    if side == -1:
        return [probabilities[2], probabilities[1], probabilities[0]]
    return [0.0, 1.0, 0.0]


def _neighbor_tiers(row: RuntimeReactionRow) -> list[Callable[[RuntimeReactionRow], bool]]:
    pair = row.pair
    horizon_minutes = row.horizon_minutes
    event_family = _event_family(row)
    scheduled_known = row.scheduled is not None

    tiers: list[Callable[[RuntimeReactionRow], bool]] = []
    if event_family is not None and scheduled_known:
        tiers.append(
            lambda candidate: candidate.pair == pair
            and candidate.horizon_minutes == horizon_minutes
            and _same_event_family(candidate, row)
            and candidate.scheduled == row.scheduled,
        )
    if event_family is not None:
        tiers.append(
            lambda candidate: candidate.pair == pair
            and candidate.horizon_minutes == horizon_minutes
            and _same_event_family(candidate, row),
        )
    if scheduled_known:
        tiers.append(
            lambda candidate: candidate.pair == pair
            and candidate.horizon_minutes == horizon_minutes
            and candidate.scheduled == row.scheduled,
        )
    tiers.append(lambda candidate: candidate.pair == pair and candidate.horizon_minutes == horizon_minutes)
    if event_family is not None and scheduled_known:
        tiers.append(
            lambda candidate: candidate.pair == pair
            and _same_event_family(candidate, row)
            and candidate.scheduled == row.scheduled,
        )
    if event_family is not None:
        tiers.append(lambda candidate: candidate.pair == pair and _same_event_family(candidate, row))
    tiers.append(lambda candidate: candidate.pair == pair)
    tiers.append(lambda candidate: candidate.horizon_minutes == horizon_minutes)
    tiers.append(lambda candidate: True)
    return tiers


def _event_family(row: RuntimeReactionRow) -> str | None:
    macro_event_type = _normalized_optional_string(row.macro_event_type)
    if macro_event_type is not None:
        return macro_event_type
    return _normalized_optional_string(row.event_category)


def _event_family_from_metadata(metadata: dict[str, str]) -> str | None:
    macro_event_type = _normalized_optional_string(metadata.get("macro_event_type"))
    if macro_event_type is not None:
        return macro_event_type
    return _normalized_optional_string(metadata.get("event_category"))


def _normalized_optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    normalized_value = value.strip().casefold()
    if not normalized_value:
        return None
    return normalized_value


def _same_event_family(left: RuntimeReactionRow, right: RuntimeReactionRow) -> bool:
    left_family = _event_family(left)
    right_family = _event_family(right)
    return left_family is not None and left_family == right_family


def _neighbor_reliability_weight(artifact: RuntimeReactionArtifact, row: RuntimeReactionRow) -> float:
    reliability_weights = artifact.reliability_weights or {}
    for key in _reliability_keys(row):
        if key in reliability_weights:
            return reliability_weights[key]
    return 1.0


def _reliability_keys(row: RuntimeReactionRow) -> list[str]:
    source = row.source_metadata.get("source", "unknown").strip().casefold() or "unknown"
    event_family = _event_family(row) or "unknown"
    pair = row.pair.strip().upper()
    return [
        f"source={source}|event_family={event_family}|pair={pair}",
        f"source={source}|event_family={event_family}",
        f"source={source}|pair={pair}",
        f"event_family={event_family}|pair={pair}",
        f"source={source}",
        f"event_family={event_family}",
        f"pair={pair}",
    ]


def _recency_weight(
    *,
    query_timestamp: datetime,
    neighbor_timestamp: datetime,
    half_life_days: float,
) -> float:
    if half_life_days <= 0:
        return 1.0
    age_days = abs((query_timestamp - neighbor_timestamp).total_seconds()) / 86400.0
    return 0.5 ** (age_days / half_life_days)


def _apply_direction_calibration(probabilities: list[float], temperature: float | None) -> list[float]:
    if temperature is None or abs(temperature - 1.0) < 1e-9:
        return probabilities
    logits = [math.log(max(probability, CALIBRATION_EPSILON)) for probability in probabilities]
    return _softmax([logit / temperature for logit in logits])


def _softmax(logits: list[float]) -> list[float]:
    if not logits:
        return []
    max_logit = max(logits)
    exp_logits = [math.exp(logit - max_logit) for logit in logits]
    denominator = sum(exp_logits)
    if denominator <= 0:
        return [0.0 for _ in logits]
    return [value / denominator for value in exp_logits]


def _structured_feature_values(
    *,
    horizon_minutes: int,
    scheduled: bool | None,
    actual: float | None,
    forecast: float | None,
    previous: float | None,
    standardized_surprise: float | None,
    include_horizon: bool,
) -> list[float]:
    resolved_surprise = _resolved_surprise_value(
        standardized_surprise=standardized_surprise,
        actual=actual,
        forecast=forecast,
        previous=previous,
    )
    normalized_surprise = 0.0 if resolved_surprise is None else _clip(resolved_surprise, -3.0, 3.0) / 3.0
    surprise_gap = 0.0
    if actual is not None and forecast is not None:
        scale = abs(forecast)
        if scale < 1e-12 and previous is not None:
            scale = abs(previous)
        if scale < 1e-12:
            scale = 1.0
        surprise_gap = _clip((actual - forecast) / scale, -3.0, 3.0) / 3.0

    return [
        min(float(horizon_minutes), 1440.0) / 1440.0 if include_horizon else 0.0,
        1.0 if scheduled else 0.0,
        normalized_surprise,
        abs(normalized_surprise),
        surprise_gap,
        1.0 if actual is not None else 0.0,
        1.0 if forecast is not None else 0.0,
        1.0 if previous is not None else 0.0,
    ]


def _resolved_surprise_value(
    *,
    standardized_surprise: float | None,
    actual: float | None,
    forecast: float | None,
    previous: float | None,
) -> float | None:
    if standardized_surprise is not None:
        return standardized_surprise
    if actual is None or forecast is None:
        return None
    scale = abs(forecast)
    if scale < 1e-12 and previous is not None:
        scale = abs(previous)
    if scale < 1e-12:
        scale = 1.0
    return (actual - forecast) / scale


def _surprise_bucket(value: float) -> str:
    if value <= -1.0:
        return "large-negative"
    if value < -0.2:
        return "negative"
    if value < 0.2:
        return "neutral"
    if value < 1.0:
        return "positive"
    return "large-positive"


def _semantic_text(
    *,
    article_text: str,
    pair: str,
    horizon_minutes: int,
    metadata: dict[str, str],
    context: AnalysisContext,
    include_horizon: bool,
) -> str:
    parts = [article_text, f"pair {pair}"]
    if include_horizon:
        parts.append(f"horizon {horizon_minutes} minutes")
    event_family = _event_family_from_metadata(metadata)
    if event_family is not None:
        parts.append(f"event family {event_family}")
    parts.append("scheduled" if context.article.scheduled else "unscheduled")
    resolved_surprise = _resolved_surprise_value(
        standardized_surprise=context.article.standardized_surprise,
        actual=context.article.actual,
        forecast=context.article.forecast,
        previous=context.article.previous,
    )
    if resolved_surprise is not None:
        parts.append(f"standardized surprise {resolved_surprise:.4f}")
    if context.article.actual is not None:
        parts.append(f"actual {context.article.actual}")
    if context.article.forecast is not None:
        parts.append(f"forecast {context.article.forecast}")
    if context.article.previous is not None:
        parts.append(f"previous {context.article.previous}")
    return " ; ".join(parts)


def _has_horizon_model(artifact: RuntimeReactionArtifact) -> bool:
    return (
        artifact.horizon_classes is not None
        and artifact.horizon_intercepts is not None
        and artifact.horizon_coefficients is not None
    )


def _predicted_horizon(artifact: RuntimeReactionArtifact, horizon_feature_vector: list[float]) -> int:
    probabilities = _horizon_probabilities(artifact, horizon_feature_vector)
    if not probabilities:
        return DEFAULT_FALLBACK_HORIZON
    best_index = max(range(len(probabilities)), key=lambda index: probabilities[index])
    return int((artifact.horizon_classes or [DEFAULT_FALLBACK_HORIZON])[best_index])


def _horizon_probabilities(artifact: RuntimeReactionArtifact, horizon_feature_vector: list[float]) -> list[float]:
    if not _has_horizon_model(artifact):
        return []
    logits: list[float] = []
    for intercept, weights in zip(artifact.horizon_intercepts or [], artifact.horizon_coefficients or []):
        logits.append(float(intercept + sum(weight * value for weight, value in zip(weights, horizon_feature_vector))))
    return _softmax(logits)


def _clip(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _direction_to_index(direction: int) -> int:
    if direction == -1:
        return 0
    if direction == 0:
        return 1
    if direction == 1:
        return 2
    raise ValueError(f"Unsupported direction label: {direction}")


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(left_value * right_value for left_value, right_value in zip(left, right))
    left_norm = sum(left_value * left_value for left_value in left) ** 0.5
    right_norm = sum(right_value * right_value for right_value in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _normalize_vector(values: list[float]) -> list[float]:
    norm = sum(value * value for value in values) ** 0.5
    if norm == 0:
        return values
    return [value / norm for value in values]


def _update_feature(values: list[float], token: str, hash_dimension: int, weight: float) -> None:
    feature_index = _stable_hash(token, hash_dimension)
    values[feature_index] += weight


def _stable_hash(token: str, hash_dimension: int) -> int:
    token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return int(token_digest[:16], 16) % hash_dimension


def _text_tokens(text: str) -> list[str]:
    normalized_text = "".join(character.lower() if character.isalnum() else " " for character in text)
    return [token for token in normalized_text.split() if token]


def _utc_now() -> datetime:
    return datetime.now(UTC)
