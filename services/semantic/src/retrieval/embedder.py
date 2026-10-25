from __future__ import annotations

import hashlib

from .text_embedder import TextEmbedder


class LocalHashingEmbedder(TextEmbedder):
    def __init__(self, dimensions: int = 256) -> None:
        """Initializes a deterministic local text embedder."""
        if dimensions <= 0:
            raise ValueError("dimensions must be positive.")
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        """Returns the dimensionality of produced embeddings."""
        return self._dimensions

    @property
    def cache_key(self) -> str:
        """Identifies this embedder so cached embeddings are never reused across configs."""
        return f"hashing:sha256:{self._dimensions}"

    def embed(self, text: str) -> list[float]:
        """Embeds text into a deterministic normalized vector."""
        values = [0.0 for _ in range(self._dimensions)]
        for token in self._tokens(text):
            feature_index = self._stable_hash(token)
            values[feature_index] += 1.0
        return self._normalize(values)

    def _tokens(self, text: str) -> list[str]:
        normalized_text = "".join(character.lower() if character.isalnum() else " " for character in text)
        return [token for token in normalized_text.split() if token]

    def _stable_hash(self, token: str) -> int:
        token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return int(token_digest[:16], 16) % self._dimensions

    def _normalize(self, values: list[float]) -> list[float]:
        norm = sum(value * value for value in values) ** 0.5
        if norm == 0:
            return values
        return [value / norm for value in values]
