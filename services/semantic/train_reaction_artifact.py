from __future__ import annotations

import argparse
import site
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_PATH = PROJECT_ROOT / "src"
USER_SITE_PATH = Path(site.getusersitepackages())
for import_path in (str(USER_SITE_PATH), str(SRC_PATH)):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from training.reaction.config import ReactionTrainingConfig
from training.reaction.export import export_reaction_artifact
from training.reaction.train import train_reaction_model


def main() -> None:
    """Parses CLI arguments and trains a local reaction artifact."""
    args = _parse_args()
    config = ReactionTrainingConfig(
        training_table_path=args.training_table.resolve(),
        output_dir=args.output_dir.resolve(),
        model_family=args.model_family,
        k_neighbors=args.k_neighbors,
        hash_dimension=args.hash_dimension,
        train_ratio=args.train_ratio,
        validation_ratio=args.validation_ratio,
        direction_neutral_threshold=args.direction_neutral_threshold,
        use_semantic_features=args.use_semantic_features,
        embedder_model=args.embedder_model,
    )
    artifact, evaluation = train_reaction_model(config)
    artifact_path = export_reaction_artifact(artifact, config, evaluation=evaluation)
    print(f"Artifact written to: {artifact_path}")
    print(f"Model family: {artifact.model_family}")
    print(f"Semantic features: {'ON' if config.use_semantic_features else 'OFF'}")
    print(f"Feature dimension: {artifact.hash_dimension}")
    print(f"Direction accuracy: {evaluation.direction_accuracy:.4f}")
    print(f"Direction macro-F1: {evaluation.direction_macro_f1:.4f}")
    print(f"Magnitude MAE: {evaluation.magnitude_mae:.4f}")
    print(f"Calibration (Brier): {evaluation.calibration_score:.4f}")
    if evaluation.horizon_prediction is not None:
        print(f"Horizon accuracy: {float(evaluation.horizon_prediction.get('accuracy', 0.0)):.4f}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train and export a local reaction artifact.")
    parser.add_argument("--training-table", type=Path, required=True, help="Path to the labeled reaction training JSONL.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory where the artifact JSON should be written.")
    parser.add_argument("--model-family", choices=["knn", "linear"], default="linear")
    parser.add_argument("--k-neighbors", type=int, default=5)
    parser.add_argument("--hash-dimension", type=int, default=256)
    parser.add_argument("--train-ratio", type=float, default=0.7)
    parser.add_argument("--validation-ratio", type=float, default=0.15)
    parser.add_argument("--direction-neutral-threshold", type=float, default=0.0005)
    parser.add_argument("--use-semantic-features", action="store_true", help="Use sentence-transformer embeddings instead of token hashing.")
    parser.add_argument("--embedder-model", default="all-MiniLM-L6-v2", help="Sentence-transformer model name.")
    return parser.parse_args()


if __name__ == "__main__":
    main()
