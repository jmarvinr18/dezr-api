"""Create documents and document_chunks for the AI assistant's search (RAG ingestion)

Revision ID: 0002_documents
Revises: 0001_cms
Create Date: 2026-10-20

On PostgreSQL this needs the pgvector extension (the pgvector/pgvector Docker image, or RDS /
Aurora with `vector` available); the migration enables it. Embeddings are a JSON column on
other databases (SQLite in tests), without the vector and full-text indexes.
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "0002_documents"
down_revision = "0001_cms"
branch_labels = None
depends_on = None

DOCUMENT_STATUSES = ("processing", "ready", "failed")
EMBEDDING_DIMENSIONS = 1024


def _in(column, values):
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade():
    postgres = op.get_bind().dialect.name == "postgresql"
    if postgres:
        available = op.get_bind().scalar(sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"))
        if not available:
            raise RuntimeError(
                "This PostgreSQL server doesn't have the pgvector extension. Use an image or service "
                "that includes it (the pgvector/pgvector:pg17 Docker image, Amazon RDS / Aurora "
                "PostgreSQL 15+), then run `flask db upgrade` again."
            )
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
        sa.Column("attempts", sa.SmallInteger(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_by", sa.String(length=255), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.CheckConstraint(_in("status", DOCUMENT_STATUSES), name="ck_documents_status"),
    )
    op.create_index("ix_documents_status_locked_until", "documents", ["status", "locked_until"])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("heading_path", sa.JSON(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("block_types", sa.JSON(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS).with_variant(sa.JSON(), "sqlite"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_document_chunks"),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name="fk_document_chunks_document_id_documents", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_document_chunk"),
    )

    if postgres:
        # Nearest-neighbour search on the vectors (cosine distance, the `<=>` operator).
        op.create_index(
            "ix_document_chunks_embedding",
            "document_chunks",
            ["embedding"],
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )
        # Keyword search, the other half of hybrid retrieval. 'simple' keeps every language's
        # words as they are (no English stemming), since the site is in four languages. Query
        # with: to_tsvector('simple', content) @@ websearch_to_tsquery('simple', :q).
        op.execute(
            "CREATE INDEX ix_document_chunks_content_fts ON document_chunks "
            "USING gin (to_tsvector('simple', content))"
        )


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index("ix_document_chunks_content_fts", table_name="document_chunks")
        op.drop_index("ix_document_chunks_embedding", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("ix_documents_status_locked_until", table_name="documents")
    op.drop_table("documents")
    # The vector extension is left installed: other objects in the database may use it.
