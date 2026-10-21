"""Hybrid search over the documents' passages, for the AI assistant (POST /api/v1/documents/search).

Two rankings of the passages of `ready` documents, merged with reciprocal rank fusion:
- semantic: nearest to the query's vector (cosine distance, the HNSW index), counting only
  documents embedded with the current model (documents.embedding_model), since vectors from
  different models aren't comparable;
- keyword: full-text matches on the GIN index ('simple' config), for exact names and numbers the
  vectors can miss; passages with all the query's words rank before those with some.
Without vectors (EMBEDDING_PROVIDER=none, or Bedrock unreachable) the keyword ranking answers
alone. PostgreSQL only: SQLite (the default test database) has neither index.
"""

import logging

from sqlalchemy import text

from app.services.errors import NotConfigured
from app.services.ingestion import Embedder, EmbeddingUnavailable

log = logging.getLogger(__name__)

# A passage scores 1 / (RRF_K + rank) in each ranking it appears in.
RRF_K = 60
# Passages each ranking contributes to the fusion.
CANDIDATES = 20

SEARCH_SQL = text(
    """
WITH semantic AS (
    SELECT id, row_number() OVER (ORDER BY distance) AS rank
    FROM (
        SELECT c.id, c.embedding <=> CAST(:vector AS vector) AS distance
        FROM document_chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE CAST(:vector AS vector) IS NOT NULL
          AND d.status = 'ready'
          AND d.embedding_model = :model
          AND c.embedding IS NOT NULL
        ORDER BY c.embedding <=> CAST(:vector AS vector)
        LIMIT :candidates
    ) nearest
),
keyword AS (
    -- Passages with every word of the query first, then those with some of them: an agent's
    -- query ("VAT rate in the Philippines") rarely has all its words in one passage.
    SELECT id, row_number() OVER (ORDER BY exact DESC, score DESC) AS rank
    FROM (
        SELECT c.id,
               to_tsvector('simple', c.content) @@ t.all_words AS exact,
               ts_rank(to_tsvector('simple', c.content), t.any_word) AS score
        FROM document_chunks c
        JOIN documents d ON d.id = c.document_id,
             (SELECT websearch_to_tsquery('simple', :query) AS all_words,
                     replace(websearch_to_tsquery('simple', :query)::text, ' & ', ' | ')::tsquery AS any_word) t
        WHERE d.status = 'ready' AND to_tsvector('simple', c.content) @@ t.any_word
        ORDER BY exact DESC, score DESC
        LIMIT :candidates
    ) matched
),
fused AS (
    SELECT id, sum(1.0 / (:rrf_k + rank)) AS score
    FROM (SELECT id, rank FROM semantic UNION ALL SELECT id, rank FROM keyword) ranked
    GROUP BY id
)
SELECT c.id, c.document_id, c.content, c.heading_path, c.page_start, c.page_end,
       d.title, d.file_name, f.score
FROM fused f
JOIN document_chunks c ON c.id = f.id
JOIN documents d ON d.id = c.document_id
ORDER BY f.score DESC, d.title, c.chunk_index
LIMIT :limit
"""
)


class DocumentSearchService:
    def __init__(self, session, embedder: Embedder):
        self._session = session
        self._embedder = embedder

    def search(self, query: str, limit: int) -> dict:
        """The passages that best match the query, best first. `mode` says whether vectors
        took part ("hybrid") or only keywords did ("keyword")."""
        if self._session.get_bind().dialect.name != "postgresql":
            raise NotConfigured("Document search needs PostgreSQL with pgvector.")
        vector = self._query_vector(query)
        rows = self._session.execute(
            SEARCH_SQL,
            {
                "vector": vector,
                "model": self._embedder.model_id,
                "query": query,
                "candidates": CANDIDATES,
                "rrf_k": RRF_K,
                "limit": limit,
            },
        ).mappings()
        return {"mode": "hybrid" if vector else "keyword", "items": [dict(row) for row in rows]}

    def _query_vector(self, query: str) -> str | None:
        """The query's vector as a pgvector literal, or None to search by keywords only."""
        if self._embedder.model_id is None:
            return None
        try:
            vector = self._embedder.embed_query(query)
        except EmbeddingUnavailable:
            log.warning("Embedding the search query failed; searching by keywords only.")
            return None
        return "[" + ",".join(map(str, vector)) + "]" if vector else None
