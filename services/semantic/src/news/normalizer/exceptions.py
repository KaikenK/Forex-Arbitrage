from __future__ import annotations


class NormalizationError(ValueError):
    def __init__(self, source_name: str, field_name: str, detail: str) -> None:
        """Initializes the normalization error message."""
        super().__init__(f"{source_name} normalization failed for field '{field_name}': {detail}")
