from __future__ import annotations

from abc import ABC
from abc import abstractmethod


class TextEmbedder(ABC):
    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Embeds a single text string into a dense vector."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Returns the dimensionality of produced embeddings."""

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embeds multiple texts. Override for efficient batched inference."""
        return [self.embed(t) for t in texts]
