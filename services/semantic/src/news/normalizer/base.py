from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from datetime import timedelta
import re

import structlog

from ..interfaces import NewsNormalizer
from ..models import UnifiedNewsEvent
from .exceptions import NormalizationError

SCHEMA_VERSION = 1
PAIR_SEPARATOR = "/"
DIRECT_PAIR_PATTERN = re.compile(r"\b([A-Z]{3})[/\-]([A-Z]{3})\b")
COMPACT_PAIR_PATTERN = re.compile(r"\b([A-Z]{6})\b")
SUPPORTED_CURRENCIES = frozenset({"AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD"})
NAME_TO_CURRENCY = {
    "euro": "EUR",
    "dollar": "USD",
    "usd": "USD",
    "yen": "JPY",
    "jpy": "JPY",
    "pound": "GBP",
    "sterling": "GBP",
    "gbp": "GBP",
    "franc": "CHF",
    "chf": "CHF",
    "aussie": "AUD",
    "aud": "AUD",
    "kiwi": "NZD",
    "nzd": "NZD",
    "cad": "CAD",
    "loonie": "CAD",
}


class BaseNormalizer(NewsNormalizer, ABC):
    SOURCE_NAME = "UNKNOWN"

    def __init__(self, watchlist: list[str]) -> None:
        """Initializes optional watchlist prioritization symbols."""
        self._watchlist = watchlist
        self._logger = structlog.get_logger().bind(component=self.__class__.__name__)

    def normalize(self, raw: dict) -> UnifiedNewsEvent:
        """Converts a raw payload into a validated unified event."""
        event = self._normalize_impl(raw)
        self._validate_event(event)
        symbols = self._extract_symbols(event.headline, event.body, self._symbol_seed(raw))
        return replace(event, symbols=symbols)

    @abstractmethod
    def _normalize_impl(self, raw: dict) -> UnifiedNewsEvent:
        """Builds a unified event from a raw payload."""

    def _symbol_seed(self, raw: dict) -> str:
        return ""

    def _validate_event(self, event: UnifiedNewsEvent) -> None:
        if not isinstance(event.headline, str) or not event.headline.strip():
            raise self._error("headline", "must be a non-empty string")

        published_at = event.published_at
        if published_at.tzinfo is None or published_at.utcoffset() is None:
            raise self._error("published_at", "must be timezone-aware UTC")
        if published_at.utcoffset() != timedelta(0):
            raise self._error("published_at", "must be in UTC")

        if event.schema_version != SCHEMA_VERSION:
            raise self._error("schema_version", f"must be {SCHEMA_VERSION}")

    def _extract_symbols(self, headline: str, body: str | None, seed: str) -> list[str]:
        searchable_text = " ".join((headline, body or "", seed))
        searchable_text_lower = searchable_text.casefold()
        symbols: list[str] = []

        for symbol in self._watchlist:
            if symbol.casefold() in searchable_text_lower and symbol not in symbols:
                symbols.append(symbol)

        for base_currency, quote_currency in DIRECT_PAIR_PATTERN.findall(searchable_text.upper()):
            if base_currency in SUPPORTED_CURRENCIES and quote_currency in SUPPORTED_CURRENCIES:
                normalized_pair = f"{base_currency}{PAIR_SEPARATOR}{quote_currency}"
                if normalized_pair not in symbols:
                    symbols.append(normalized_pair)

        for compact_pair in COMPACT_PAIR_PATTERN.findall(searchable_text.upper()):
            base_currency = compact_pair[:3]
            quote_currency = compact_pair[3:]
            if base_currency in SUPPORTED_CURRENCIES and quote_currency in SUPPORTED_CURRENCIES:
                normalized_pair = f"{base_currency}{PAIR_SEPARATOR}{quote_currency}"
                if normalized_pair not in symbols:
                    symbols.append(normalized_pair)

        mentioned_currencies: list[str] = []
        for currency_name, currency_code in NAME_TO_CURRENCY.items():
            if currency_name in searchable_text_lower and currency_code not in mentioned_currencies:
                mentioned_currencies.append(currency_code)

        if len(mentioned_currencies) >= 2 and not symbols:
            for index, base_currency in enumerate(mentioned_currencies[:-1]):
                quote_currency = mentioned_currencies[index + 1]
                normalized_pair = f"{base_currency}{PAIR_SEPARATOR}{quote_currency}"
                if normalized_pair not in symbols:
                    symbols.append(normalized_pair)

        return symbols

    def _error(self, field_name: str, detail: str) -> NormalizationError:
        return NormalizationError(self.SOURCE_NAME, field_name, detail)

    def _require_field(self, raw: dict, field_name: str) -> object:
        if field_name not in raw or raw[field_name] is None:
            raise self._error(field_name, "is required")
        return raw[field_name]

    def _require_string(self, raw: dict, field_name: str) -> str:
        value = self._require_field(raw, field_name)
        if not isinstance(value, str):
            raise self._error(field_name, "must be a string")
        return value

    def _optional_string(self, raw: dict, field_name: str) -> str | None:
        value = raw.get(field_name)
        if value is None:
            return None
        if not isinstance(value, str):
            raise self._error(field_name, "must be a string")
        return value

    def _now_utc(self) -> datetime:
        return datetime.now(UTC)
