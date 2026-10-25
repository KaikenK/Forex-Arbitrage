from __future__ import annotations

from dataclasses import asdict
from dataclasses import is_dataclass
from datetime import datetime
from enum import Enum
import json
from pathlib import Path
from typing import Any
from typing import cast

from training.backtest.metrics import BacktestSummary

DEFAULT_REPORT_FILENAME = "backtest_report.json"


def build_report_payload(summary: BacktestSummary) -> dict[str, object]:
    """Builds a compact JSON-friendly payload for a backtest summary."""
    return cast(dict[str, object], _json_compatible(summary))


def write_backtest_report(summary: BacktestSummary, output_dir: Path) -> Path:
    """Writes a compact local backtest report for later inspection."""
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / DEFAULT_REPORT_FILENAME
    report_path.write_text(json.dumps(build_report_payload(summary), indent=2), encoding="utf-8")
    return report_path


def _json_compatible(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_compatible(asdict(cast(Any, value)))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    return value
