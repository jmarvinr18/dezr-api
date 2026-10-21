"""AI assistant conversations: ai_sessions, ai_messages

Revision ID: 0003_ai_chat
Revises: 0002_documents
Create Date: 2026-10-21

"""
from alembic import op
import sqlalchemy as sa


revision = "0003_ai_chat"
down_revision = "0002_documents"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("preview", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ai_sessions"),
    )
    op.create_index("ix_ai_sessions_updated_at", "ai_sessions", ["updated_at"])

    op.create_table(
        "ai_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=True),
        # Reported by the agent for the answer, for cost tracking.
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("tool_calls", sa.Integer(), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("steps", sa.JSON(), nullable=True),
        sa.Column("todos", sa.JSON(), nullable=True),
        sa.Column("client_ip_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ai_messages"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["ai_sessions.id"], name="fk_ai_messages_session_id_ai_sessions", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("session_id", "sequence", name="uq_ai_messages_session_sequence"),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_ai_messages_role"),
    )
    op.create_index("ix_ai_messages_created_at", "ai_messages", ["created_at"])
    op.create_index(
        "ix_ai_messages_client_ip_hash_created_at", "ai_messages", ["client_ip_hash", "created_at"]
    )


def downgrade():
    op.drop_index("ix_ai_messages_client_ip_hash_created_at", table_name="ai_messages")
    op.drop_index("ix_ai_messages_created_at", table_name="ai_messages")
    op.drop_table("ai_messages")
    op.drop_index("ix_ai_sessions_updated_at", table_name="ai_sessions")
    op.drop_table("ai_sessions")
