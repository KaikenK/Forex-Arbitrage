from __future__ import annotations

import structlog

from ..models import ContextTag


def default_keyword_map() -> dict[ContextTag, list[str]]:
    return {
        ContextTag.CENTRAL_BANK: [
            "fed",
            "federal reserve",
            "ecb",
            "boe",
            "boj",
            "rbi",
            "rate decision",
            "interest rate",
            "powell",
            "lagarde",
            "das",
            "monetary policy",
        ],
        ContextTag.GEOPOLITICAL: ["war", "sanctions", "conflict", "election", "geopolit", "missile", "nuclear", "tariff", "tension"],
        ContextTag.EARNINGS: ["earnings", "revenue", "profit", "eps", "guidance", "beat", "miss"],
        ContextTag.MACRO: ["gdp", "inflation", "cpi", "unemployment", "jobs report", "pmi", "payroll", "retail sales"],
        ContextTag.COMMODITY: ["oil", "crude", "gold", "brent", "opec", "commodity", "energy"],
        ContextTag.FISCAL: ["budget", "fiscal deficit", "gst", "tax reform", "government spending"],
        ContextTag.POLICY: ["regulation", "reform", "law", "sanction", "trade war"],
        ContextTag.RISK_SENTIMENT: ["risk-off", "risk-on", "safe haven", "crash", "correction", "bear market", "volatility"],
    }


class ContextTagger:
    def __init__(self, keyword_map: dict[ContextTag, list[str]] | None = None) -> None:
        """Initializes the context tagger keyword map."""
        self._keyword_map = keyword_map if keyword_map is not None else default_keyword_map()
        self._logger = structlog.get_logger().bind(component=self.__class__.__name__)

    def tag(self, headline: str) -> ContextTag:
        """Tags a headline with the first matching context."""
        normalized_headline = headline.casefold()
        for context_tag, keywords in self._keyword_map.items():
            for keyword in keywords:
                if keyword.casefold() in normalized_headline:
                    return context_tag
        return ContextTag.OTHER
