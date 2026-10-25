"""Train the pooled multi-pair reaction model: USD/INR rows + US calendar-release rows.

Split (the part that makes the holdout honest):
  - Each data stream (USD/INR archive, calendar releases) is split chronologically on its
    own, 70/15/15 by *release*, so the four pair-rows, three horizons and two views of one
    release never straddle partitions (`release_grouped_time_split`). A single global time
    cut cannot work here: the calendar ends 2025-04 and the USD/INR archive starts 2025-01,
    so a global cut would leave USD/INR with no training rows at all.
  - Cross-stream purge: an evaluation row (validation/test) of one stream is dropped if any
    training row of the other stream falls on the same UTC date — the same macro shock must
    not be learnt through USD/INR and then scored through EUR/USD.

Model selection: class weighting is chosen on the *validation* partitions only; the test
partitions are evaluated once, for the chosen model. The hash dimension is fixed at 4,096
by design, not by validation: at 256 buckets real live-feed words collided with the words
that carry a surprise's sign ("iran" shared a bucket with "hotter", "reuters" with
"misses"), which an in-distribution validation set cannot reveal.

USD/INR drift: 603 of the 743 USD/INR rows come from April 2026, when the rupee fell
steadily (1440m labels: 214 up, 26 down). Raw labels would teach "any real-looking
headline -> USD up". Each USD/INR label is therefore taken relative to the mean USD/INR
return at that horizon over the USD/INR *training* partition (no test information).

Text tokens are scoped by pair group (`--text-token-scope pair_group`, the default): USD/INR
rows and the majors learn separate text weights, while structured, pair, horizon and
metadata tokens stay shared. With shared text weights, words found only in the April-2026
USD/INR headlines ("iran", "war", "oil", "reuters") set the majors' direction for ~75% of
real feed headlines. `--text-token-scope shared` reproduces the earlier model.

Labels are USD-oriented (see training/calendar_reaction_rows.py); USD/INR rows are already
USD-oriented because USD is the base. The exported artifact says so
(`direction_orientation="usd"`) and the live scorer flips direction back per pair.

Metadata tokens: `source`, `event_category`, `macro_event_type`. `standardized_surprise`
(one unique token per value — noise for a hashed linear model) and `scheduled` (already a
structured feature and token; as a metadata token it fired in training but never for live
headlines, which carry no scheduled flag) are left out.

Run (from services/semantic):
  python scripts/train_pooled_reaction_model.py [--ship-to <artifact path> ...]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

SERVICE_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = SERVICE_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from training.calendar_reaction_rows import surprise_word_direction  # noqa: E402
from training.reaction.config import ReactionTrainingConfig  # noqa: E402
from training.reaction.export import export_reaction_artifact  # noqa: E402
from training.reaction.train import (  # noqa: E402
    CALIBRATION_EPSILON,
    ReactionFeatureRow,
    _direction_label,
    _direction_probabilities,
    assemble_reaction_features,
    load_reaction_rows,
    release_group_key,
    release_grouped_time_split,
    train_reaction_model_from_splits,
)

METADATA_FIELDS = ["source", "event_category", "macro_event_type"]
HASH_DIMENSION = 4096
CLASS_WEIGHTS: tuple[str, ...] = ("balanced", "none")
DEFAULT_USDINR = SERVICE_ROOT / "data" / "usdinr_reaction_training_rows_combined.jsonl"
DEFAULT_CALENDAR = SERVICE_ROOT / "data" / "calendar_reaction_rows.jsonl"
DEFAULT_OUTPUT_DIR = SERVICE_ROOT / "artifacts" / "pooled_multipair"
DIRECTIONS = (-1, 0, 1)


def main() -> int:
    args = _parse_args()
    base_config = ReactionTrainingConfig(
        training_table_path=args.usdinr_rows,
        output_dir=args.output_dir,
        model_family="linear",
        metadata_fields=METADATA_FIELDS,
        hash_dimension=HASH_DIMENSION,
        text_token_scope=args.text_token_scope,
    )
    streams = {
        "usdinr": _feature_rows(base_config, args.usdinr_rows, dataset="usdinr"),
        "calendar": _feature_rows(base_config, args.calendar_rows, dataset="calendar"),
    }
    splits = {name: release_grouped_time_split(rows, 0.7, 0.15) for name, rows in streams.items()}
    splits, drift_bps = _drift_adjust(splits, "usdinr", base_config.direction_neutral_threshold)
    splits, purged = _purge_cross_stream_dates(splits)
    print(f"USD/INR drift removed (bps, from its training partition): {drift_bps}")

    grid_results = []
    best = None
    for class_weight in CLASS_WEIGHTS:
        config = base_config.model_copy(update={"direction_class_weight": class_weight})
        hash_dimension = config.hash_dimension
        train = [row for parts in splits.values() for row in parts[0]]
        validation = [row for parts in splits.values() for row in parts[1]]
        artifact = train_reaction_model_from_splits(config, train, validation, direction_orientation="usd")
        _check_vectorised_matches_library(artifact, validation)
        validation_scores = {
            name: _accuracy([r.direction_label for r in parts[1]], _predict(artifact, parts[1])[0])
            for name, parts in splits.items()
        }
        pooled = _accuracy([r.direction_label for r in validation], _predict(artifact, validation)[0])
        result = {"hash_dimension": hash_dimension, "class_weight": class_weight,
                  "validation_accuracy_pooled": round(pooled, 4),
                  "validation_accuracy_by_stream": {k: round(v, 4) for k, v in validation_scores.items()},
                  "temperature": artifact.direction_calibration_temperature}
        grid_results.append(result)
        print(f"grid {hash_dimension:>5} {class_weight:<9} val acc pooled {pooled:.4f} {result['validation_accuracy_by_stream']}")
        if best is None or pooled > best[0] + 1e-9:
            best = (pooled, config, artifact, splits, purged, train, result)

    _, config, artifact, splits, purged, train, chosen = best
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "orientation": "usd",
        "metadata_fields": METADATA_FIELDS,
        "hash_dimension": HASH_DIMENSION,
        "text_token_scope": args.text_token_scope,
        "usdinr_drift_removed_bps": drift_bps,
        "model_selection": {"grid": grid_results, "chosen": chosen, "criterion": "pooled validation accuracy"},
        "split": {name: _split_summary(parts) for name, parts in splits.items()},
        "purged_eval_rows": purged,
        "direction_calibration_temperature": artifact.direction_calibration_temperature,
        "test": {name: _evaluate_groups(artifact, train, parts[2]) for name, parts in splits.items()},
        "benchmarks": _benchmarks(train, splits),
    }
    report["test_prediction_balance"] = _prediction_balance(
        artifact, [row for parts in splits.values() for row in parts[2]])

    extra = {
        "training_manifest": {
            "usdinr_rows": _display_path(args.usdinr_rows),
            "calendar_rows": _display_path(args.calendar_rows),
            "split": report["split"],
            "purged_eval_rows": purged,
            "usdinr_drift_removed_bps": drift_bps,
            "model_selection": report["model_selection"],
            "trained_at": report["generated_at"],
        },
        "holdout_report": report["test"],
    }
    artifact_path = export_reaction_artifact(artifact, config, extra_payload=extra)
    report_path = args.output_dir / "training_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nartifact -> {artifact_path} ({artifact_path.stat().st_size / 1024:.1f} KB)")
    print(f"report   -> {report_path}")
    _print_report(report)

    for target in args.ship_to or []:
        _ship(artifact_path, target)
    return 0


def _feature_rows(config: ReactionTrainingConfig, path: Path, *, dataset: str) -> list[ReactionFeatureRow]:
    """Features via the library (identical to what the live scorer computes), stored as
    float32 arrays so a 1,024-dim feature set for ~30k rows stays near 250 MB."""
    rows = load_reaction_rows(config.model_copy(update={"training_table_path": path}))
    feature_rows = []
    for row in rows:
        metadata = dict(row.source_metadata)
        metadata.setdefault("dataset", dataset)
        metadata.setdefault("row_view", "archive")
        # Kept for baselines and drift adjustment only; not model features.
        metadata["article_text"] = row.article_text
        metadata["raw_reaction_label"] = repr(row.reaction_label)
        feature_row = assemble_reaction_features(replace(row, source_metadata=metadata), config)
        feature_rows.append(replace(
            feature_row,
            feature_vector=np.asarray(feature_row.feature_vector, dtype=np.float32),
            horizon_feature_vector=np.asarray(feature_row.horizon_feature_vector, dtype=np.float32),
        ))
    return feature_rows


def _drift_adjust(splits, stream: str, neutral_threshold: float):
    """Relabel `stream` relative to its own per-horizon mean training return."""
    train = splits[stream][0]
    sums: dict[int, list[float]] = {}
    for row in train:
        sums.setdefault(row.horizon_minutes, []).append(float(row.source_metadata["raw_reaction_label"]))
    drift = {horizon: sum(values) / len(values) for horizon, values in sums.items()}

    def relabel(rows):
        adjusted = []
        for row in rows:
            excess = float(row.source_metadata["raw_reaction_label"]) - drift.get(row.horizon_minutes, 0.0)
            adjusted.append(replace(row, direction_label=_direction_label(excess, neutral_threshold)))
        return adjusted

    splits = dict(splits)
    splits[stream] = tuple(relabel(part) for part in splits[stream])
    return splits, {str(h): round(v * 1e4, 2) for h, v in sorted(drift.items())}


def _purge_cross_stream_dates(splits):
    train_dates = {name: {row.timestamp.astimezone(UTC).date() for row in parts[0]} for name, parts in splits.items()}
    purged: dict[str, dict[str, int]] = {}
    cleaned = {}
    for name, (train, validation, test) in splits.items():
        other_dates = set().union(*(dates for other, dates in train_dates.items() if other != name))
        keep = lambda rows: [row for row in rows if row.timestamp.astimezone(UTC).date() not in other_dates]  # noqa: E731
        kept_validation, kept_test = keep(validation), keep(test)
        purged[name] = {"validation": len(validation) - len(kept_validation), "test": len(test) - len(kept_test)}
        cleaned[name] = (train, kept_validation, kept_test)
    return cleaned, purged


def _predict(artifact, rows: list[ReactionFeatureRow]) -> tuple[list[int], np.ndarray, np.ndarray]:
    """Vectorised equivalent of train._direction_probabilities / _linear_predicted_magnitude."""
    if not rows:
        return [], np.zeros((0, 3)), np.zeros(0)
    x = np.stack([np.asarray(row.feature_vector, dtype=np.float64) for row in rows])
    logits = x @ np.asarray(artifact.direction_coefficients).T + np.asarray(artifact.direction_intercepts)
    raw = _softmax(logits)
    probabilities = np.zeros((len(rows), 3))
    for column, direction in enumerate(artifact.direction_classes):
        probabilities[:, DIRECTIONS.index(direction)] = raw[:, column]
    temperature = artifact.direction_calibration_temperature
    if temperature is not None and abs(temperature - 1.0) >= 1e-9:
        probabilities = _softmax(np.log(np.maximum(probabilities, CALIBRATION_EPSILON)) / temperature)
    directions = [DIRECTIONS[int(index)] for index in probabilities.argmax(axis=1)]
    magnitudes = x @ np.asarray(artifact.magnitude_coefficients) + artifact.magnitude_intercept
    return directions, probabilities, magnitudes


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = np.exp(logits - logits.max(axis=1, keepdims=True))
    return shifted / shifted.sum(axis=1, keepdims=True)


def _check_vectorised_matches_library(artifact, rows: list[ReactionFeatureRow]) -> None:
    sample = rows[:: max(1, len(rows) // 25)][:25]
    _, vectorised, _ = _predict(artifact, sample)
    for row, fast in zip(sample, vectorised):
        slow = _direction_probabilities(artifact, row)
        if not np.allclose(fast, slow, atol=1e-6):
            raise AssertionError(f"vectorised prediction diverged from the library: {fast} vs {slow}")


def _split_summary(parts) -> dict[str, dict[str, object]]:
    summary = {}
    for label, rows in zip(("train", "validation", "test"), parts):
        summary[label] = {
            "rows": len(rows),
            "releases": len({release_group_key(row) for row in rows}),
            "start": min(row.timestamp for row in rows).isoformat() if rows else None,
            "end": max(row.timestamp for row in rows).isoformat() if rows else None,
        }
    return summary


def _evaluate_groups(artifact, train: list[ReactionFeatureRow], test: list[ReactionFeatureRow]) -> dict[str, object]:
    """Per (pair, view) and (pair, view, horizon): accuracy, macro-F1, MAE, three baselines."""
    groups: dict[str, list[ReactionFeatureRow]] = {}
    for row in test:
        view = row.source_metadata.get("row_view", "archive")
        groups.setdefault(f"{row.pair}|{view}", []).append(row)
        groups.setdefault(f"{row.pair}|{view}|{row.horizon_minutes}m", []).append(row)
    majority = _majority_by_pair(train)
    results: dict[str, object] = {}
    for key in sorted(groups):
        rows = groups[key]
        truth = [row.direction_label for row in rows]
        predicted, _, magnitudes = _predict(artifact, rows)
        word_rule = [surprise_word_direction(row.source_metadata.get("article_text", "")) for row in rows]
        majority_class = majority.get(rows[0].pair, 0)
        results[key] = {
            "rows": len(rows),
            "releases": len({release_group_key(row) for row in rows}),
            "direction_accuracy": round(_accuracy(truth, predicted), 4),
            "direction_macro_f1": round(_macro_f1(truth, predicted), 4),
            "majority_class": majority_class,
            "majority_baseline_accuracy": round(_accuracy(truth, [majority_class] * len(rows)), 4),
            "surprise_word_rule_accuracy": round(_accuracy(truth, word_rule), 4),
            "sign_hit_rate_nonneutral": _sign_hit_rate(truth, predicted),
            "magnitude_mae_bps": round(float(np.mean(np.abs(magnitudes - np.array([r.magnitude_label for r in rows])))) * 1e4, 2),
            "mean_abs_return_bps": round(float(np.mean([r.magnitude_label for r in rows])) * 1e4, 2),
            "predicted_direction_counts": {str(k): v for k, v in Counter(predicted).items()},
            "true_direction_counts": {str(k): v for k, v in Counter(truth).items()},
        }
    return results


def _sign_hit_rate(truth: list[int], predicted: list[int]) -> dict[str, float] | None:
    """Among rows where both prediction and truth are up/down: share with the same sign."""
    both = [(t, p) for t, p in zip(truth, predicted) if t and p]
    if not both:
        return None
    return {"rate": round(sum(t == p for t, p in both) / len(both), 4), "n": len(both)}


def _majority_by_pair(train: list[ReactionFeatureRow]) -> dict[str, int]:
    counts: dict[str, Counter] = {}
    for row in train:
        counts.setdefault(row.pair, Counter())[row.direction_label] += 1
    return {pair: counter.most_common(1)[0][0] for pair, counter in counts.items()}


def _prediction_balance(artifact, rows: list[ReactionFeatureRow]) -> dict[str, dict[str, int]]:
    balance: dict[str, Counter] = {}
    for row, direction in zip(rows, _predict(artifact, rows)[0]):
        balance.setdefault(row.source_metadata.get("row_view", "archive"), Counter())[str(direction)] += 1
    return {view: dict(counter) for view, counter in balance.items()}


def _benchmarks(train: list[ReactionFeatureRow], splits) -> dict[str, object]:
    """Same split, two reference models: k-NN (k=5, cosine) on the same features, and a
    linear SVM on TF-IDF text + the structured features (the earlier benchmark family)."""
    from scipy.sparse import csr_matrix, hstack
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.svm import LinearSVC

    train_x = np.stack([row.feature_vector for row in train]).astype(np.float32)
    train_x /= np.maximum(np.linalg.norm(train_x, axis=1, keepdims=True), 1e-10)
    train_y = np.array([row.direction_label for row in train])

    texts = lambda rows: [row.source_metadata.get("article_text", "") for row in rows]  # noqa: E731
    structured = lambda rows: csr_matrix(np.stack([np.asarray(row.feature_vector[-8:]) for row in rows]))  # noqa: E731
    vectorizer = TfidfVectorizer(max_features=4096, ngram_range=(1, 2), sublinear_tf=True)
    svm_train = hstack([vectorizer.fit_transform(texts(train)), structured(train)], format="csr")
    svm = LinearSVC(C=0.75, class_weight="balanced", max_iter=5000).fit(svm_train, train_y)

    results: dict[str, object] = {}
    for name, (_, _, test) in splits.items():
        if not test:
            continue
        test_x = np.stack([row.feature_vector for row in test]).astype(np.float32)
        test_x /= np.maximum(np.linalg.norm(test_x, axis=1, keepdims=True), 1e-10)
        knn_pred: list[int] = []
        for start in range(0, len(test_x), 512):
            similarity = test_x[start:start + 512] @ train_x.T
            top = np.argpartition(similarity, -5, axis=1)[:, -5:]
            for row_index, neighbours in enumerate(top):
                weights: Counter = Counter()
                for neighbour in neighbours:
                    weights[int(train_y[neighbour])] += max(float(similarity[row_index, neighbour]), 0.0)
                knn_pred.append(weights.most_common(1)[0][0] if weights else 0)
        svm_pred = svm.predict(hstack([vectorizer.transform(texts(test)), structured(test)], format="csr")).tolist()
        by_key: dict[str, dict[str, list[int]]] = {}
        for row, knn_label, svm_label in zip(test, knn_pred, svm_pred):
            cell = by_key.setdefault(f"{row.pair}|{row.source_metadata.get('row_view', 'archive')}",
                                     {"truth": [], "knn": [], "svm": []})
            cell["truth"].append(row.direction_label)
            cell["knn"].append(knn_label)
            cell["svm"].append(int(svm_label))
        results[name] = {
            key: {"knn_accuracy": round(_accuracy(c["truth"], c["knn"]), 4),
                  "svm_tfidf_accuracy": round(_accuracy(c["truth"], c["svm"]), 4)}
            for key, c in sorted(by_key.items())
        }
    return results


def _accuracy(truth: list[int], predicted: list[int]) -> float:
    return sum(t == p for t, p in zip(truth, predicted)) / len(truth) if truth else 0.0


def _macro_f1(truth: list[int], predicted: list[int]) -> float:
    scores = []
    for label in DIRECTIONS:
        tp = sum(t == label and p == label for t, p in zip(truth, predicted))
        fp = sum(t != label and p == label for t, p in zip(truth, predicted))
        fn = sum(t == label and p != label for t, p in zip(truth, predicted))
        scores.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return sum(scores) / len(scores)


def _print_report(report: dict[str, object]) -> None:
    print("\nsplit:", json.dumps(report["split"], default=str))
    print("purged eval rows:", report["purged_eval_rows"])
    print("chosen:", report["model_selection"]["chosen"])
    print(f"\n{'group':<32}{'rows':>6}{'rel':>5}{'acc':>7}{'maj':>7}{'word':>7}{'f1':>7}{'mae':>7}{'|r|':>7}  knn / svm")
    for stream, groups in report["test"].items():
        bench = report["benchmarks"].get(stream, {})
        for key, cell in groups.items():
            if key.count("|") != 1:
                continue
            b = bench.get(key, {})
            print(f"{stream + ':' + key:<32}{cell['rows']:>6}{cell['releases']:>5}{cell['direction_accuracy']:>7.3f}"
                  f"{cell['majority_baseline_accuracy']:>7.3f}{cell['surprise_word_rule_accuracy']:>7.3f}"
                  f"{cell['direction_macro_f1']:>7.3f}{cell['magnitude_mae_bps']:>7.1f}{cell['mean_abs_return_bps']:>7.1f}"
                  f"  {b.get('knn_accuracy', float('nan')):.3f} / {b.get('svm_tfidf_accuracy', float('nan')):.3f}")
    print("\npredicted direction balance on test:", report["test_prediction_balance"])


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    return str(resolved.relative_to(SERVICE_ROOT)) if resolved.is_relative_to(SERVICE_ROOT) else str(resolved)


def _ship(artifact_path: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = target.with_name(target.name + ".pre-pooled.bak")
        if not backup.exists():
            shutil.copy2(target, backup)
            print(f"backed up {target} -> {backup.name}")
    shutil.copy2(artifact_path, target)
    print(f"shipped -> {target}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the pooled multi-pair reaction model.")
    parser.add_argument("--usdinr-rows", type=Path, default=DEFAULT_USDINR)
    parser.add_argument("--calendar-rows", type=Path, default=DEFAULT_CALENDAR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--text-token-scope", choices=["shared", "pair_group"], default="pair_group")
    parser.add_argument("--ship-to", type=Path, action="append",
                        help="Copy the artifact here (repeatable); an existing file is backed up once.")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
