"""Hybrid search over the documents' passages, for the AI assistant (POST /api/v1/documents/search).

Two rankings of the passages of `ready` documents, merged with reciprocal rank fusion:
- semantic: nearest to the query's vector (cosine distance, the HNSW index), counting only
  documents embedded with the current model (documents.embedding_model), since vectors from
  different models aren't comparable;
- keyword: full-text matches on the GIN index ('simple' config), for exact names and numbers the
  vectors can miss; passages with all the query's words rank before those with some.
Without vectors (EMBEDDING_PROVIDER=none, or the embedding service unreachable) the keyword
ranking answers alone. PostgreSQL only: SQLite (the default test database) has neither index.

With RETRIEVAL_BACKEND=bedrock_kb the Bedrock Knowledge Base answers instead (mode
"knowledge_base"), limited to documents that still exist here; when it can't be reached, the
keyword ranking above does.
"""

import logging

from sqlalchemy import select, text

from app.model import Document
from app.services.errors import NotConfigured
from app.services.ingestion import Embedder, EmbeddingUnavailable
from app.services.knowledge_base import BedrockKnowledgeBase, KnowledgeBaseUnavailable

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
    def __init__(self, session, embedder: Embedder, knowledge_base: BedrockKnowledgeBase | None = None):
        self._session = session
        self._embedder = embedder
        self._knowledge_base = knowledge_base

    def search(self, query: str, limit: int) -> dict:
        """The passages that best match the query, best first. `mode` says what answered:
        vectors and keywords ("hybrid"), keywords only ("keyword"), or the knowledge base."""
        if self._knowledge_base is not None:
            try:
                return {"mode": "knowledge_base", "items": self._from_knowledge_base(query, limit)}
            except KnowledgeBaseUnavailable:
                log.warning("Knowledge base search failed; searching by keywords only.")
                return self._search_here(query, limit, use_vectors=False)
        return self._search_here(query, limit)

    def _from_knowledge_base(self, query: str, limit: int) -> list[dict]:
        passages = [p for p in self._knowledge_base.retrieve(query, limit) if p.document_id and p.content]
        ids = {p.document_id for p in passages}
        documents = {
            d.id: d
            for d in self._session.scalars(select(Document).where(Document.id.in_(ids), Document.status == "ready"))
        } if ids else {}
        return [
            {
                "id": None,
                "document_id": p.document_id,
                "content": p.content,
                "heading_path": [],
                "page_start": p.page,
                "page_end": p.page,
                "title": documents[p.document_id].title,
                "file_name": documents[p.document_id].file_name,
                "score": p.score,
            }
            for p in passages
            # Deleted here but still in the index (its removal failed, or is in progress).
            if p.document_id in documents
        ]

    def _search_here(self, query: str, limit: int, use_vectors: bool = True) -> dict:
        if self._session.get_bind().dialect.name != "postgresql":
            raise NotConfigured("Document search needs PostgreSQL with pgvector.")
        vector = self._query_vector(query) if use_vectors else None
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
