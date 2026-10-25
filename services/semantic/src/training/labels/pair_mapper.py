from __future__ import annotations


class PairMapper:
    def map_pairs(self, event_symbols: list[str], candidate_symbols: list[str]) -> list[str]:
        """Returns unique candidate pairs for an article."""
        normalized_pairs: list[str] = []
        for symbol in event_symbols + candidate_symbols:
            normalized_symbol = symbol.strip()
            if not normalized_symbol:
                continue
            if normalized_symbol not in normalized_pairs:
                normalized_pairs.append(normalized_symbol)
        return normalized_pairs
