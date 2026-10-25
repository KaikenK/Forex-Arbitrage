from __future__ import annotations

from sentence_transformers import SentenceTransformer

from .text_embedder import TextEmbedder


class SentenceTransformerEmbedder(TextEmbedder):
    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        """Initializes a sentence-transformer backed embedder."""
        self._model_name = model_name
        self._model = SentenceTransformer(model_name)
        self._dimensions = self._model.get_sentence_embedding_dimension()

    @property
    def dimensions(self) -> int:
        """Returns the dimensionality of produced embeddings."""
        return self._dimensions

    @property
    def cache_key(self) -> str:
        """Identifies this model so cached embeddings are never reused across models."""
        return f"sentence-transformer:{self._model_name}:{self._dimensions}"

    def embed(self, text: str) -> list[float]:
        """Embeds text into a dense semantic vector."""
        vector = self._model.encode(text, normalize_embeddings=True)
        return vector.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embeds multiple texts efficiently in a single batch."""
        vectors = self._model.encode(texts, normalize_embeddings=True, batch_size=64)
        return [v.tolist() for v in vectors]
