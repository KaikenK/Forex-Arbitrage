"""
Frozen event schema for the onshore-offshore USD/INR basis pipeline.

This is the contract between the detection side (owned here) and the semantic
engine (separate owner). It is published to the Redis stream ``arbex.raw_opps``
and mirrored to ``research/results/**/events.jsonl``.

`docs/SPEC.md` section 7 is the human-readable spec; this module is the
machine-checkable version. **Do not change field names or types without bumping
SCHEMA_VERSION and telling the semantic-engine owner.**
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any, Dict, Optional

SCHEMA_VERSION = "1.1"   # 1.1: + composite_score (additive, back-compatible)
RAW_STREAM = "arbex.raw_opps"
SCORED_STREAM = "arbex.scored_opps"

LEG_PAIRS = ("onshore_offshore", "onshore_otc", "offshore_otc")
PERSISTENCE_CLASSES = ("ephemeral", "flickering", "persistent")
EXECUTION_VERDICTS = ("viable", "risky", "unlikely", "unknown")


@dataclass
class BasisEvent:
    """One detected USD/INR basis dislocation. All fields are part of the frozen schema."""

    # identity / timing
    event_id: str
    ts: float                       # detection time, epoch seconds
    symbol: str                     # "USDINR"

    # what & where
    leg_pair: str                   # one of LEG_PAIRS
    buy_leg: str                    # the cheap leg ("onshore" | "offshore" | "otc")
    sell_leg: str
    buy_source_id: str
    sell_source_id: str

    # the number
    basis_pips: float               # executable basis: (sell_bid - buy_ask) / pip
    comparison_basis: str           # "futures" (Option A) | "implied_spot"
    target_expiry: str              # ISO date of T*
    carry_adjustment_pips: float    # summed |carry adj| applied across both legs

    # provenance
    buy_quote_ts: float
    sell_quote_ts: float
    offshore_staleness_ms: int = 0

    # scoring (filled progressively; defaults for a raw event)
    persistence_class: str = "ephemeral"
    execution_verdict: str = "unknown"
    expected_slippage_pips: float = 0.0
    assumed_depth: bool = True
    raw_score: float = 0.0            # gross basis, pre-cost
    composite_score: float = 0.0     # score_basis_event() output (v1.1)

    # meta
    schema_version: str = SCHEMA_VERSION
    cadence: str = "tick"           # "tick" | "eod"

    # ------------------------------------------------------------------
    @classmethod
    def new(cls, **kw: Any) -> "BasisEvent":
        kw.setdefault("event_id", str(uuid.uuid4()))
        kw.setdefault("ts", time.time())
        kw.setdefault("symbol", "USDINR")
        if isinstance(kw.get("target_expiry"), date):
            kw["target_expiry"] = kw["target_expiry"].isoformat()
        ev = cls(**kw)
        ev.validate()
        return ev

    def validate(self) -> None:
        if self.leg_pair not in LEG_PAIRS:
            raise ValueError(f"leg_pair {self.leg_pair!r} not in {LEG_PAIRS}")
        if self.persistence_class not in PERSISTENCE_CLASSES:
            raise ValueError(f"bad persistence_class {self.persistence_class!r}")
        if self.execution_verdict not in EXECUTION_VERDICTS:
            raise ValueError(f"bad execution_verdict {self.execution_verdict!r}")
        if self.comparison_basis not in ("futures", "implied_spot"):
            raise ValueError(f"bad comparison_basis {self.comparison_basis!r}")
        for f in ("basis_pips", "carry_adjustment_pips", "raw_score",
                  "expected_slippage_pips", "ts", "buy_quote_ts", "sell_quote_ts"):
            if not isinstance(getattr(self, f), (int, float)):
                raise ValueError(f"{f} must be numeric")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_stream_fields(self) -> Dict[str, str]:
        """Redis stream field map — one JSON payload under a versioned key."""
        return {"schema": self.schema_version, "payload": json.dumps(self.to_dict())}

    @classmethod
    def from_stream_fields(cls, fields: Dict[str, str]) -> "BasisEvent":
        data = json.loads(fields["payload"])
        data.pop("schema_version", None)
        return cls(schema_version=fields.get("schema", SCHEMA_VERSION), **data)
