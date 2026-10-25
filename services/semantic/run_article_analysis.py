from __future__ import annotations

import argparse
import asyncio
import json
import site
import sys
from dataclasses import asdict
from dataclasses import is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_PATH = PROJECT_ROOT / "src"
USER_SITE_PATH = Path(site.getusersitepackages())
for import_path in (str(USER_SITE_PATH), str(SRC_PATH)):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from market.runtime_factory import build_runtime_service
from market.schemas import ArticleInput
from news.models import SourceType


def main() -> None:
    """Parses CLI arguments and runs one artifact-backed article analysis."""
    args = _parse_args()
    article_input = _article_input_from_args(args)

    embedder = None
    if args.use_sentence_transformer:
        from retrieval.sentence_transformer_embedder import SentenceTransformerEmbedder
        embedder = SentenceTransformerEmbedder()

    service = build_runtime_service(
        reaction_artifact_path=args.reaction_artifact.resolve(),
        training_table_path=args.training_table.resolve() if args.training_table is not None else None,
        preferred_horizon_minutes=args.horizon,
        embedder=embedder,
        use_rag=args.use_rag,
        use_finbert=args.use_finbert,
    )
    analysis = asyncio.run(service.analyze_article(article_input))
    print(json.dumps(_to_jsonable(analysis), indent=2))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run article analysis with a trained reaction artifact.")
    parser.add_argument("--reaction-artifact", type=Path, required=True, help="Path to reaction_model_artifact.json.")
    parser.add_argument("--training-table", type=Path, help="Optional JSONL training table for evidence retrieval.")
    parser.add_argument("--headline", help="Headline to analyze.")
    parser.add_argument("--body", default="", help="Optional article body.")
    parser.add_argument("--pair", help="Optional pair hint such as EUR/USD.")
    parser.add_argument("--source", choices=[source.value for source in SourceType], default=SourceType.RSS.value)
    parser.add_argument("--event-category", help="Optional event category metadata.")
    parser.add_argument("--macro-event-type", help="Optional macro event type metadata.")
    parser.add_argument("--published-at", help="Optional ISO timestamp with timezone, for example 2026-03-25T12:00:00+00:00.")
    parser.add_argument("--horizon", type=int, help="Optional preferred horizon in minutes.")
    parser.add_argument("--use-rag", action="store_true", help="Enable RAG-enhanced reaction scoring.")
    parser.add_argument("--use-sentence-transformer", action="store_true", help="Use sentence-transformer embeddings instead of hashing.")
    parser.add_argument("--use-finbert", action="store_true", help="Use FinBERT transformer for sentiment instead of keyword baseline.")
    return parser.parse_args()


def _article_input_from_args(args: argparse.Namespace) -> ArticleInput:
    headline = args.headline or input("Headline: ").strip()
    if not headline:
        raise SystemExit("A headline is required.")

    body = args.body.strip()
    article_text = headline if not body else f"{headline}\n{body}"
    published_at = None
    if args.published_at:
        normalized_value = args.published_at.replace("Z", "+00:00")
        published_at = datetime.fromisoformat(normalized_value)
        if published_at.tzinfo is None or published_at.utcoffset() is None:
            raise SystemExit("--published-at must be timezone-aware.")

    return ArticleInput(
        article_text=article_text,
        headline=headline,
        source=SourceType(args.source),
        published_at=published_at,
        event_category=args.event_category,
        macro_event_type=args.macro_event_type,
        currency_pair=args.pair,
    )


def _to_jsonable(value: object) -> object:
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    return value


if __name__ == "__main__":
    main()
