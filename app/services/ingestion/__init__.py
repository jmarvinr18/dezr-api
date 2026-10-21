"""Turning uploaded documents into searchable passages: parse (parsers/), chunk (chunker.py),
embed (embeddings.py). services/ingestion_service.py runs the steps for a stored document."""

from .chunker import Chunk, ChunkingOptions, HybridChunker, contextualize
from .embeddings import BedrockEmbedder, Embedder, NoEmbedder, build_embedder
from .errors import EmbeddingUnavailable, UnreadableDocument
from .parsers import parse_document
