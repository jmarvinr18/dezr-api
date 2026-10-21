from pgvector.sqlalchemy import Vector

from app.database.db import db
from app.constants import DOCUMENT_STATUSES, EMBEDDING_DIMENSIONS
from app.model.admin_user import utcnow
from app.model.site_setting import _in
import uuid

# pgvector on PostgreSQL, plain JSON elsewhere (SQLite in tests).
EmbeddingVector = Vector(EMBEDDING_DIMENSIONS).with_variant(db.JSON(), "sqlite")


class Document(db.Model):
    """A file the AI assistant can search (Admin > Documents). The bytes live in S3 / on disk;
    its passages are in document_chunks."""

    __tablename__ = "documents"
    __table_args__ = (
        db.CheckConstraint(_in("status", DOCUMENT_STATUSES), name="ck_documents_status"),
        db.Index("ix_documents_status_locked_until", "status", "locked_until"),
    )

    id = db.Column(db.Uuid, primary_key=True, default=uuid.uuid4)
    title = db.Column(db.String(200), nullable=False)
    # The name it was uploaded with; display only, never part of the storage key.
    file_name = db.Column(db.String(255), nullable=False)
    # pdf, docx, txt or md, recognised from the content.
    kind = db.Column(db.String(10), nullable=False)
    content_type = db.Column(db.String(100), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    storage_key = db.Column(db.String(500), nullable=False)

    status = db.Column(db.String(20), nullable=False, default="processing")
    # Why processing failed, in words an admin can act on.
    error = db.Column(db.String(500))
    chunk_count = db.Column(db.Integer)
    # The embedding model the chunks' vectors came from; null when stored without vectors.
    embedding_model = db.Column(db.String(100))

    # Job bookkeeping: a processor claims the document until locked_until; a claim that runs
    # out (the process died) is picked up again, up to INGESTION_MAX_ATTEMPTS times.
    attempts = db.Column(db.SmallInteger, nullable=False, default=0)
    locked_until = db.Column(db.DateTime(timezone=True))

    uploaded_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    uploaded_by = db.Column(db.String(255))
    processed_at = db.Column(db.DateTime(timezone=True))


class DocumentChunk(db.Model):
    """One passage of a document, sized for retrieval, with the context it came from."""

    __tablename__ = "document_chunks"
    __table_args__ = (
        db.UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_document_chunk"),
        db.Index(
            "ix_document_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id = db.Column(db.Uuid, primary_key=True, default=uuid.uuid4)
    # Deleted with the document (the service removes chunks in bulk first; the cascade is a backstop).
    document_id = db.Column(db.Uuid, db.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    chunk_index = db.Column(db.Integer, nullable=False)
    # The passage itself, without its headings.
    content = db.Column(db.Text, nullable=False)
    # Headings it sits under, outermost first: ["Corporate", "Incorporation"].
    heading_path = db.Column(db.JSON, nullable=False, default=list)
    # PDF pages the passage spans (1-based); null for formats without pages.
    page_start = db.Column(db.Integer)
    page_end = db.Column(db.Integer)
    # Approximate token count of `content` (see services/ingestion/text.py).
    token_count = db.Column(db.Integer, nullable=False)
    # Block kinds it holds: paragraph, list, table, code, quote.
    block_types = db.Column(db.JSON, nullable=False, default=list)
    # Vector of the passage with its context (title + headings); null without an embedding provider.
    embedding = db.Column(EmbeddingVector)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
