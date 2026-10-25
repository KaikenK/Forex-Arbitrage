# Project: Forex Market Intelligence Pipeline

## Product goal
This codebase analyzes a single article, headline, or news event and produces a
structured forex market intelligence view.

Required outputs:
- sentiment score
- market reaction score
- likely affected currency pairs or symbols
- confidence
- explanation backed by retrieved evidence
- risk flags
- key drivers

## System scope
The legacy news sentiment pipeline is now a foundation, not the final product.
The system should reuse ingestion, normalization, queueing, and low-level
analysis where useful, but the main problem is article-to-market-impact
analysis.

## Top-level packages
1. **News Foundation** (`src/news/`)
   - Ingestion, normalization, queueing, source adapters, and foundation event
     contracts.
   - Produces `UnifiedNewsEvent`.

2. **Retrieval Layer** (`src/retrieval/`)
   - Retrieves evidence and context for an article or event.
   - Used for explanation quality, traceability, and context enrichment.

3. **Market Intelligence Layer** (`src/market/`)
   - Produces market reaction outputs.
   - Responsible for sentiment, market score, affected symbols, confidence,
     risk flags, and key drivers.
   - Includes:
     - article preprocessing and context-building services
     - pair and entity inference
     - sentiment and market-reaction orchestration
     - final aggregation into `ArticleAnalysis`

4. **Training Layer** (`src/training/`)
   - Offline training data contracts, labeling, evaluation fixtures, and model
     scoring benchmarks.
   - Includes:
     - dataset manifests and downloaders
     - sentiment training
     - market-reaction training
     - walk-forward backtesting and report generation

5. **API Layer** (`src/api/`)
   - Request and response contracts for article analysis services.
   - Includes asynchronous JSON endpoints for:
     - article analysis submission
     - recent analysis history
     - health status
     - metrics inspection

6. **Frontend** (`frontend/`)
   - User-facing application for article submission, evidence inspection,
     risk review, and result exploration.
   - Includes:
     - article input panel
     - result summary cards
     - evidence and risk review panels
     - historical reaction visualization

## Core contracts (never change without updating all consumers)
```python
@dataclass(frozen=True)
class UnifiedNewsEvent:
    event_id: str
    schema_version: int
    source: SourceType
    headline: str
    body: str | None
    published_at: datetime  # UTC, timezone-aware
    symbols: list[str]      # extracted or inferred references; may be empty
    raw_sentiment: float    # optional foundation score, not final output
    sentiment_source: SentimentSource
    ingested_at: datetime   # UTC

@dataclass(frozen=True)
class RetrievedEvidence:
    evidence_id: str
    source: str
    evidence_type: EvidenceType
    title: str
    snippet: str
    url: str | None
    published_at: datetime | None
    relevance_score: float

@dataclass(frozen=True)
class MarketReactionSignal:
    event_id: str
    symbol: str
    direction: float        # -1.0 to 1.0
    magnitude: float        # >= 0.0
    confidence: float       # 0.0 to 1.0
    horizon_minutes: int
    key_drivers: list[str]
    produced_at: datetime   # UTC

@dataclass(frozen=True)
class ArticleAnalysis:
    event_id: str
    sentiment_score: float
    market_score: float
    affected_symbols: list[str]
    confidence: float
    evidence: list[RetrievedEvidence]
    risk_flags: list[RiskFlag]
    produced_at: datetime   # UTC
    key_drivers: list[KeyDriver]
```

## Retrieval-first explanation rule
- Retrieval is required for explanation, traceability, and evidence display.
- Explanations must be backed by retrieved evidence, not free-form narration.
- Retrieved evidence may enrich scoring inputs, but evidence retrieval is first
  an explanation and auditability layer.

## Strict RAG rule
- RAG is for context, evidence, and explanation support.
- RAG is not the primary predictor.
- Final market scores must not be simple restatements of retrieved text.
- Predictive logic must remain in dedicated analyzers or models, with retrieval
  used to support, justify, or challenge outputs.

## Watchlist rule
- Watchlists may be used for prioritization, ranking, alert routing, and UI
  filtering.
- Watchlists must not be the only path by which an article gets analyzed or
  scored.
- Articles without a watchlist match may still receive full analysis if the
  system can infer affected symbols or material market context.

## Training and evaluation
- `src/training/` owns offline datasets, labels, feature contracts, evaluation
  fixtures, and benchmark definitions.
- Training labels must support at least sentiment, market reaction, affected
  symbols, and confidence calibration.
- Evaluation must separate:
  - retrieval quality
  - explanation quality
  - sentiment quality
  - market reaction quality
- Backtesting must support walk-forward validation and pair-level reporting.
- Offline evaluation artifacts must be reproducible and versioned.
- Compact local reports must be JSON-serializable so the frontend can consume
  them later without reformatting.

## API rules
- `src/api/` defines service boundaries only.
- API contracts must expose article analysis inputs and structured outputs.
- API handlers must not hide missing evidence, low confidence, or risk flags.
- API routes call market analysis services and adapters only.
- API routes must not contain predictive model logic.

## Frontend rules
- `frontend/` presents article analysis, evidence, risk flags, and market view.
- The frontend may use watchlists for prioritization and filtering only.
- The frontend must display evidence separately from model conclusions.
- The frontend should consume the async JSON API, not call model code directly.

## Language & stack
- Python 3.12
- asyncio throughout
- Type hints on every function signature
- pytest + pytest-asyncio for tests
- structlog for logging, never print()
- pydantic-settings for config

## Design rules
- No global state. Pass dependencies via constructor injection.
- No bare except. Catch specific exception types only.
- No magic strings. Use enums or constants modules.
- Interfaces first - abstract base class before any concrete implementation.
- Single responsibility - one class per file.
- No circular imports. Direction: `api -> market -> retrieval -> news`.
  `training` may depend on shared contracts but not runtime services.
- Dead code is deleted, not commented out.
- Every public method has a one-line docstring.

## Anti-patterns to reject
- Treating watchlists as the scoring gate
- Using RAG retrieval as the sole predictor
- Returning an explanation without traceable evidence
- Polling without backoff
- Catching Exception or BaseException broadly
- Mutable default arguments
- Shared mutable state between workers
- Hard-coded API keys or URLs
- Silent failures (always log + reraise or return a Result type)

## Migration notes
- The legacy news pipeline remains a valid ingestion and normalization
  foundation.
- Watchlists are now optional prioritization metadata only.
- Symbol and pair inference must work even without a watchlist match.
- Deprecated watchlist-gated behavior must be removed or explicitly documented.

## Local demo workflow
- Run the news foundation with a local TOML config when validating ingestion.
- Run the market analysis service and API locally with injected demo
  scorers/retrievers.
- Serve `frontend/` as a static app against that local API for demo flows.
