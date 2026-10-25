from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
import re
import socket
from typing import Any
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import urlopen

from .usdinr_dataset_builder import STANDARDIZED_SURPRISE_FORMULA

TRADING_ECONOMICS_GUEST_CREDENTIALS = "guest:guest"
DEFAULT_MIN_IMPORTANCE = 2
DEFAULT_SYMBOLS = ["USD/INR"]

UNIT_MULTIPLIERS: dict[str, float] = {
    "K": 1_000.0,
    "M": 1_000_000.0,
    "B": 1_000_000_000.0,
    "T": 1_000_000_000_000.0,
}


@dataclass(frozen=True)
class CalendarEventRule:
    country: str
    categories: tuple[str, ...]
    event_category: str
    macro_event_type: str
    event_patterns: tuple[str, ...] = ()

    def matches(self, row: dict[str, Any]) -> bool:
        country = str(row.get("Country", "")).strip().casefold()
        category = str(row.get("Category", "")).strip().casefold()
        event_name = str(row.get("Event", "")).strip().casefold()
        if country != self.country.casefold():
            return False
        if category not in {value.casefold() for value in self.categories}:
            return False
        if not self.event_patterns:
            return True
        return any(pattern.casefold() in event_name for pattern in self.event_patterns)


TRACKED_EVENT_RULES: tuple[CalendarEventRule, ...] = (
    CalendarEventRule("United States", ("Non Farm Payrolls",), "macro", "nfp"),
    CalendarEventRule("United States", ("Unemployment Rate",), "macro", "unemployment"),
    CalendarEventRule("United States", ("Inflation Rate",), "macro", "cpi"),
    CalendarEventRule("United States", ("Core Inflation Rate",), "macro", "core-cpi"),
    CalendarEventRule("United States", ("Retail Sales MoM",), "macro", "retail-sales"),
    CalendarEventRule("United States", ("GDP Growth Rate",), "macro", "gdp"),
    CalendarEventRule("United States", ("Interest Rate",), "central-bank", "rate-decision", ("fed interest rate decision",)),
    CalendarEventRule("United States", ("Interest Rate",), "central-bank", "fed-press-conference", ("fed press conference",)),
    CalendarEventRule("India", ("Interest Rate",), "central-bank", "rate-decision"),
    CalendarEventRule("India", ("Inflation Rate",), "macro", "cpi"),
    CalendarEventRule("India", ("GDP Growth Rate",), "macro", "gdp"),
    CalendarEventRule("India", ("Balance of Trade",), "macro", "trade-balance"),
)


def fetch_usdinr_calendar_rows(
    start_date: date,
    end_date: date,
    *,
    min_importance: int = DEFAULT_MIN_IMPORTANCE,
    fetch_json: callable | None = None,
) -> list[dict[str, Any]]:
    """Fetches public Trading Economics calendar rows relevant to USD/INR macro drivers."""
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date.")

    resolved_fetch_json = fetch_json or _fetch_json
    rows_by_key: dict[str, dict[str, Any]] = {}
    for trading_day in iter_calendar_days(start_date, end_date):
        for row in _fetch_us_rows(trading_day, min_importance=min_importance, fetch_json=resolved_fetch_json):
            calendar_id = _calendar_identity(row)
            rows_by_key[calendar_id] = row
        for row in _fetch_india_rows(trading_day, min_importance=min_importance, fetch_json=resolved_fetch_json):
            calendar_id = _calendar_identity(row)
            rows_by_key[calendar_id] = row
    return sorted(rows_by_key.values(), key=_calendar_sort_key)


def convert_calendar_rows_to_seeds(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Converts Trading Economics calendar rows into timestamped USD/INR event seeds."""
    seeds: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    for row in rows:
        rule = classify_calendar_row(row)
        if rule is None:
            continue
        seed = calendar_row_to_seed(row, rule)
        if seed["event_id"] in seen_event_ids:
            continue
        seen_event_ids.add(seed["event_id"])
        seeds.append(seed)
    return sorted(seeds, key=lambda seed: str(seed["published_at"]))


def classify_calendar_row(row: dict[str, Any]) -> CalendarEventRule | None:
    for rule in TRACKED_EVENT_RULES:
        if rule.matches(row):
            return rule
    return None


def calendar_row_to_seed(row: dict[str, Any], rule: CalendarEventRule) -> dict[str, Any]:
    published_at = _parse_calendar_timestamp(_required_string(row, "Date"))
    actual_display = _string_or_none(row.get("Actual"))
    forecast_display = _string_or_none(row.get("Forecast"))
    previous_display = _string_or_none(row.get("Previous"))
    actual = parse_numeric_value(actual_display, unit=_string_or_none(row.get("Unit")))
    forecast = parse_numeric_value(forecast_display, unit=_string_or_none(row.get("Unit")))
    previous = parse_numeric_value(previous_display, unit=_string_or_none(row.get("Unit")))
    standardized_surprise = resolve_standardized_surprise(actual=actual, forecast=forecast, previous=previous)
    event_name = _required_string(row, "Event")
    country = _required_string(row, "Country")
    headline = _build_headline(country=country, event_name=event_name, actual=actual_display, forecast=forecast_display)
    body = _build_body(
        country=country,
        event_name=event_name,
        actual=actual_display,
        forecast=forecast_display,
        previous=previous_display,
        reference=_string_or_none(row.get("Reference")),
        source_name=_string_or_none(row.get("Source")),
    )
    calendar_id = _required_string(row, "CalendarId")
    calendar_source = f"https://api.tradingeconomics.com/calendar/calendarid/{calendar_id}?c={TRADING_ECONOMICS_GUEST_CREDENTIALS}"
    seed: dict[str, Any] = {
        "event_id": _seed_event_id(country=country, macro_event_type=rule.macro_event_type, published_at=published_at, calendar_id=calendar_id),
        "headline": headline,
        "body": body,
        "published_at": published_at.isoformat(),
        "source": "rss",
        "sentiment_label": 0.0,
        "symbols": list(DEFAULT_SYMBOLS),
        "event_category": rule.event_category,
        "macro_event_type": rule.macro_event_type,
        "scheduled": True,
        "actual": actual,
        "forecast": forecast,
        "previous": previous,
        "standardized_surprise": standardized_surprise,
        "calendar_source": calendar_source,
        "source_url": _string_or_none(row.get("SourceURL")),
        "source_metadata": {
            "consensus_source": "tradingeconomics_guest_api",
            "calendar_event_id": calendar_id,
            "calendar_reference": _string_or_none(row.get("Reference")) or "",
            "calendar_symbol": _string_or_none(row.get("Symbol")) or "",
            "calendar_url": _string_or_none(row.get("URL")) or "",
            "calendar_last_update_utc": _string_or_none(row.get("LastUpdate")) or "",
            "calendar_country": country,
            "calendar_category": _required_string(row, "Category"),
            "calendar_event_name": event_name,
            "calendar_import_source": _calendar_import_source(country),
            "value_unit": _string_or_none(row.get("Unit")) or "",
            "ticker": _string_or_none(row.get("Ticker")) or "",
            "actual_display": actual_display or "",
            "forecast_display": forecast_display or "",
            "previous_display": previous_display or "",
            "surprise_formula": STANDARDIZED_SURPRISE_FORMULA,
        },
    }
    return seed


def load_seed_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"Seed file {path} contains a non-object line.")
        records.append(payload)
    return records


def merge_seed_records(existing_records: list[dict[str, Any]], imported_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merges imported records into existing seeds without overwriting curated duplicates."""
    merged_by_event_id: dict[str, dict[str, Any]] = {}
    for record in imported_records:
        event_id = _required_string(record, "event_id")
        merged_by_event_id[event_id] = record
    for record in existing_records:
        event_id = _required_string(record, "event_id")
        merged_by_event_id[event_id] = record
    return sorted(merged_by_event_id.values(), key=lambda record: str(record.get("published_at", "")))


def write_seed_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def summarize_seed_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    macro_counts: dict[str, int] = {}
    country_counts: dict[str, int] = {}
    for record in records:
        macro_event_type = str(record.get("macro_event_type", "unknown"))
        macro_counts[macro_event_type] = macro_counts.get(macro_event_type, 0) + 1
        source_metadata = record.get("source_metadata", {})
        if isinstance(source_metadata, dict):
            country = str(source_metadata.get("calendar_country", "unknown"))
            country_counts[country] = country_counts.get(country, 0) + 1
    return {
        "records": len(records),
        "macro_event_types": dict(sorted(macro_counts.items())),
        "countries": dict(sorted(country_counts.items())),
    }


def iter_calendar_days(start_date: date, end_date: date) -> list[date]:
    days: list[date] = []
    current_day = start_date
    while current_day <= end_date:
        if current_day.weekday() < 5:
            days.append(current_day)
        current_day += timedelta(days=1)
    return days


def parse_numeric_value(raw_value: str | None, *, unit: str | None = None) -> float | None:
    if raw_value is None:
        return None
    text = raw_value.strip()
    if not text:
        return None
    normalized = text.replace(",", "").replace("−", "-").upper()
    suffix = normalized[-1] if normalized[-1] in UNIT_MULTIPLIERS else ""
    if suffix:
        normalized = normalized[:-1]
    normalized = normalized.replace("%", "")
    numeric_match = re.search(r"[-+]?\d+(?:\.\d+)?", normalized)
    if numeric_match is None:
        return None
    value = float(numeric_match.group())
    multiplier = UNIT_MULTIPLIERS.get(suffix or (unit or "").strip().upper(), 1.0)
    return value * multiplier


def resolve_standardized_surprise(*, actual: float | None, forecast: float | None, previous: float | None) -> float | None:
    if actual is None or forecast is None:
        return None
    scale = abs(forecast)
    if scale < 1e-12 and previous is not None:
        scale = abs(previous)
    if scale < 1e-12:
        scale = 1.0
    return (actual - forecast) / scale


def _fetch_us_rows(trading_day: date, *, min_importance: int, fetch_json: callable) -> list[dict[str, Any]]:
    us_importance = max(min_importance, 3)
    country_url = (
        "https://api.tradingeconomics.com/calendar/country/"
        f"{quote('united states')}/{trading_day.isoformat()}/{trading_day.isoformat()}"
        f"?c={TRADING_ECONOMICS_GUEST_CREDENTIALS}&f=json&importance={us_importance}"
    )
    all_url = (
        "https://api.tradingeconomics.com/calendar/country/All/"
        f"{trading_day.isoformat()}/{trading_day.isoformat()}"
        f"?c={TRADING_ECONOMICS_GUEST_CREDENTIALS}&f=json&importance={us_importance}"
    )
    combined_rows = _safe_validated_rows(country_url, fetch_json)
    combined_rows.extend(
        row
        for row in _safe_validated_rows(all_url, fetch_json)
        if str(row.get("Country", "")).strip() == "United States"
    )
    return combined_rows


def _fetch_india_rows(trading_day: date, *, min_importance: int, fetch_json: callable) -> list[dict[str, Any]]:
    url = (
        "https://api.tradingeconomics.com/calendar/country/All/"
        f"{trading_day.isoformat()}/{trading_day.isoformat()}"
        f"?c={TRADING_ECONOMICS_GUEST_CREDENTIALS}&f=json&importance={min_importance}"
    )
    rows = _safe_validated_rows(url, fetch_json)
    return [row for row in rows if str(row.get("Country", "")).strip() == "India"]


def _validated_rows(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("Trading Economics response must decode to a list.")
    rows: list[dict[str, Any]] = []
    for item in payload:
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _safe_validated_rows(url: str, fetch_json: callable) -> list[dict[str, Any]]:
    try:
        return _validated_rows(fetch_json(url))
    except HTTPError as exc:
        if exc.code in {403, 404}:
            return []
        raise
    except (TimeoutError, socket.timeout, URLError):
        return []


def _fetch_json(url: str) -> object:
    with urlopen(url, timeout=15) as response:
        return json.load(response)


def _calendar_identity(row: dict[str, Any]) -> str:
    calendar_id = _required_string(row, "CalendarId")
    row_date = _required_string(row, "Date")
    return f"{calendar_id}:{row_date}"


def _calendar_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return (_required_string(row, "Date"), _required_string(row, "CalendarId"))


def _seed_event_id(*, country: str, macro_event_type: str, published_at: datetime, calendar_id: str) -> str:
    country_slug = "us" if country.casefold() == "united states" else "india"
    return f"usdinr-{country_slug}-{macro_event_type}-{published_at.date().isoformat()}-{calendar_id}"


def _build_headline(*, country: str, event_name: str, actual: str | None, forecast: str | None) -> str:
    if actual and forecast:
        return f"{country} {event_name} released at {actual} versus {forecast} forecast"
    if actual:
        return f"{country} {event_name} released at {actual}"
    return f"{country} {event_name} release"


def _build_body(
    *,
    country: str,
    event_name: str,
    actual: str | None,
    forecast: str | None,
    previous: str | None,
    reference: str | None,
    source_name: str | None,
) -> str:
    summary_parts = [f"{country} {event_name} update"]
    if reference:
        summary_parts.append(f"reference period {reference}")
    if actual:
        summary_parts.append(f"actual {actual}")
    if forecast:
        summary_parts.append(f"forecast {forecast}")
    if previous:
        summary_parts.append(f"previous {previous}")
    if source_name:
        summary_parts.append(f"source {source_name}")
    return ". ".join(summary_parts) + "."


def _parse_calendar_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _calendar_import_source(country: str) -> str:
    if country.casefold() == "india":
        return "tradingeconomics_guest_api_country_all_daily"
    return "tradingeconomics_guest_api_country_daily"


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = _string_or_none(payload.get(key))
    if value is None:
        raise ValueError(f"Missing required field '{key}'.")
    return value


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)
    stripped_value = value.strip()
    if not stripped_value:
        return None
    return stripped_value