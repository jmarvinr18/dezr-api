"""Processing uploaded documents: read the stored file, parse it, chunk it (hybrid), embed the
passages and store them. The documents table is the queue: a `processing` document is waiting
or in progress.

A processor claims a document by setting `locked_until` (a lease). If the process dies, the
lease runs out and another processor picks the document up again; after INGESTION_MAX_ATTEMPTS
claims it is marked failed, so one bad file can't crash workers forever. Several processors
(threads, worker containers) can run at once: a claim is a single conditional UPDATE, so only
one of them gets each document.

Where the passages become searchable depends on the settings: vectors in pgvector from the
EMBEDDING_PROVIDER's model, or a Bedrock Knowledge Base (RETRIEVAL_BACKEND=bedrock_kb). The
document's `embedding_model` records which (the model id, or "bedrock-kb:<id>"), so after a
switch `requeue_stale` finds the documents to process again.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, insert, or_, select, update
from sqlalchemy.orm import Session

from app.model import Document, DocumentChunk
from app.services.ingestion import (
    EmbeddingUnavailable,
    Embedder,
    HybridChunker,
    UnreadableDocument,
    contextualize,
    parse_document,
)
from app.services.knowledge_base import BedrockKnowledgeBase, KnowledgeBaseRejected, KnowledgeBaseUnavailable
from app.services.storage import MediaStorage, StorageError, StoredObjectNotFound

log = logging.getLogger(__name__)

NO_TEXT = "The document has no text to search."
EMBEDDING_FAILED = (
    "The embedding service is unavailable, so the document couldn't be made searchable. "
    "Delete it and upload it again later."
)
KNOWLEDGE_BASE_DOWN = (
    "The knowledge base is unavailable, so the document couldn't be made searchable. "
    "It is tried again when the search settings change, or reprocess it later."
)
KNOWLEDGE_BASE_REJECTED = "The knowledge base couldn't index the document: {reason}"
STORE_FAILED = "The file store is unavailable. Delete the document and upload it again later."
FILE_MISSING = "The stored file is missing. Delete the document and upload it again."
GAVE_UP = (
    "Processing stopped unexpectedly several times. Delete the document and upload it again, "
    "or try a smaller file."
)
UNEXPECTED = "The document could not be processed. Delete it and upload it again."


def _now():
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class _Claimed:
    id: uuid.UUID
    title: str
    kind: str
    storage_key: str
    attempts: int


class IngestionService:
    def __init__(
        self,
        session: Session,
        storage: MediaStorage,
        embedder: Embedder,
        chunker: HybridChunker,
        *,
        lease_minutes: int = 15,
        max_attempts: int = 3,
        knowledge_base: BedrockKnowledgeBase | None = None,
    ):
        self._session = session
        self._storage = storage
        self._embedder = embedder
        self._chunker = chunker
        self._lease = timedelta(minutes=lease_minutes)
        self._max_attempts = max_attempts
        self._knowledge_base = knowledge_base

    @property
    def index_id(self) -> str | None:
        """Where new passages become searchable: the knowledge base, or the embedding model
        (None without vectors)."""
        if self._knowledge_base is not None:
            return self._knowledge_base.index_id
        return self._embedder.model_id

    # ---------- Queue ----------

    def run_pending(self, limit: int | None = None) -> int:
        """Processes waiting documents, oldest first, until none are left (or `limit`).
        Returns how many this call processed."""
        done, skipped = 0, set()
        while limit is None or done < limit:
            document_id = self.next_pending()
            if document_id is None or document_id in skipped:
                break
            if self.process(document_id) == "skipped":
                skipped.add(document_id)
            else:
                done += 1
        return done

    def next_pending(self) -> uuid.UUID | None:
        return self._session.scalar(
            select(Document.id)
            .where(Document.status == "processing", self._claimable())
            .order_by(Document.uploaded_at)
            .limit(1)
        )

    def has_unclaimed(self) -> bool:
        return self.next_pending() is not None

    def requeue(self, document: Document) -> None:
        """Processes a document again (after a chunking change, or to add vectors)."""
        document.status = "processing"
        document.error = None
        document.attempts = 0
        document.locked_until = None
        self._session.commit()

    def requeue_stale(self) -> int:
        """Queues the documents to process again after the embedding model or the retrieval
        backend changed: ready documents indexed elsewhere, and documents that failed because
        the embedding service or knowledge base was down. Returns how many."""
        current = self.index_id
        if current is None:
            # Keyword search only: nothing to re-embed, and dropping vectors gains nothing.
            return 0
        count = self._session.execute(
            update(Document)
            .where(
                or_(
                    (Document.status == "ready") & Document.embedding_model.is_distinct_from(current),
                    (Document.status == "failed") & Document.error.in_([EMBEDDING_FAILED, KNOWLEDGE_BASE_DOWN]),
                )
            )
            .values(status="processing", error=None, attempts=0, locked_until=None)
            .execution_options(synchronize_session=False)
        ).rowcount
        self._session.commit()
        if count:
            log.info("Queued %d document(s) to index with %s", count, current)
        return count

    def _claimable(self):
        return or_(Document.locked_until.is_(None), Document.locked_until < _now())

    def _claim(self, document_id: uuid.UUID) -> _Claimed | None:
        claimed = self._session.execute(
            update(Document)
            .where(Document.id == document_id, Document.status == "processing", self._claimable())
            .values(locked_until=_now() + self._lease, attempts=Document.attempts + 1)
            .execution_options(synchronize_session=False)
        ).rowcount
        self._session.commit()
        if claimed != 1:
            return None
        row = self._session.execute(
            select(Document.id, Document.title, Document.kind, Document.storage_key, Document.attempts)
            .where(Document.id == document_id)
        ).one_or_none()
        return _Claimed(*row) if row else None

    # ---------- Processing ----------

    def process(self, document_id: uuid.UUID) -> str:
        """ready, failed, cancelled (deleted meanwhile) or skipped (someone else has it)."""
        document = self._claim(document_id)
        if document is None:
            return "skipped"
        if document.attempts > self._max_attempts:
            return self._fail(document.id, GAVE_UP)
        try:
            stream = self._storage.open(document.storage_key)
            try:
                data = stream.read()
            finally:
                stream.close()
            chunks = self._chunker.chunk(parse_document(document.kind, data))
            if not chunks:
                raise UnreadableDocument(NO_TEXT)
            if not self._exists(document.id):
                return "cancelled"
            if self._knowledge_base is not None:
                self._knowledge_base.put(document.id, document.kind, document.title, data)
                result = self._store(document.id, chunks, None, self._knowledge_base.index_id)
                if result == "cancelled":
                    self._knowledge_base.remove(document.id, document.kind)
                return result
            vectors = self._embedder.embed([contextualize(document.title, chunk) for chunk in chunks])
            return self._store(document.id, chunks, vectors, self._embedder.model_id if vectors else None)
        except UnreadableDocument as exc:
            return self._fail(document.id, exc.message)
        except StoredObjectNotFound:
            return "cancelled" if not self._exists(document.id) else self._fail(document.id, FILE_MISSING)
        except StorageError:
            log.exception("Reading document %s from storage failed", document.id)
            return self._fail(document.id, STORE_FAILED)
        except EmbeddingUnavailable:
            return self._fail(document.id, EMBEDDING_FAILED)
        except KnowledgeBaseUnavailable:
            return self._fail(document.id, KNOWLEDGE_BASE_DOWN)
        except KnowledgeBaseRejected as exc:
            return self._fail(document.id, KNOWLEDGE_BASE_REJECTED.format(reason=exc.reason)[:500])
        except Exception:
            log.exception("Processing document %s failed", document.id)
            self._session.rollback()
            return self._fail(document.id, UNEXPECTED)

    def _exists(self, document_id: uuid.UUID) -> bool:
        return self._session.scalar(select(Document.id).where(Document.id == document_id)) is not None

    def _store(self, document_id: uuid.UUID, chunks, vectors, index_id: str | None) -> str:
        # Lock the row: a delete that commits first leaves nothing to lock (cancelled); one that
        # comes later waits, then removes these passages with the document.
        document = self._session.scalar(select(Document).where(Document.id == document_id).with_for_update())
        if document is None or document.status != "processing":
            self._session.rollback()
            return "cancelled"
        self._session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        self._session.execute(
            insert(DocumentChunk),
            [
                {
                    "document_id": document_id,
                    "chunk_index": index,
                    "content": chunk.content,
                    "heading_path": chunk.heading_path,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "token_count": chunk.token_count,
                    "block_types": chunk.block_types,
                    "embedding": vectors[index] if vectors else None,
                }
                for index, chunk in enumerate(chunks)
            ],
        )
        document.status = "ready"
        document.chunk_count = len(chunks)
        document.embedding_model = index_id
        document.error = None
        document.locked_until = None
        document.processed_at = _now()
        self._session.commit()
        log.info("Document %s ready: %d passages", document_id, len(chunks))
        return "ready"

    def _fail(self, document_id: uuid.UUID, message: str) -> str:
        self._session.execute(
            update(Document)
            .where(Document.id == document_id, Document.status == "processing")
            .values(status="failed", error=message, chunk_count=None, locked_until=None, processed_at=_now())
            .execution_options(synchronize_session=False)
        )
        self._session.commit()
        log.info("Document %s failed: %s", document_id, message)
        return "failed"
