from .embedder import LocalHashingEmbedder
from .index import LocalEvidenceIndex
from .retriever import HistoricalEvidenceRetriever
from .schemas import RetrievalDocument
from .schemas import RetrievalResult
from .text_embedder import TextEmbedder

__all__ = [
    "HistoricalEvidenceRetriever",
    "LocalEvidenceIndex",
    "LocalHashingEmbedder",
    "RetrievalDocument",
    "RetrievalResult",
    "SentenceTransformerEmbedder",
    "TextEmbedder",
]


def __getattr__(name: str):
    """SentenceTransformerEmbedder on first use only: importing it pulls in sentence_transformers,
    torch and transformers (~17 s and ~440 MB), which every service start paid even with
    embeddings off."""
    if name == "SentenceTransformerEmbedder":
        from .sentence_transformer_embedder import SentenceTransformerEmbedder
        return SentenceTransformerEmbedder
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
