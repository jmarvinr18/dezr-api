from app.database.db import db
from app.model.admin_user import utcnow
from app.model.site_setting import _in
import uuid


class AiSession(db.Model):
    """A visitor's conversation with the AI assistant.

    The agent runs as a separate service (dezr-ai on Bedrock AgentCore); this is the site's own
    transcript of it. Visitors are anonymous: knowing the conversation's id (a random UUID the
    browser keeps) is what lets them continue it."""

    __tablename__ = "ai_sessions"
    __table_args__ = (db.Index("ix_ai_sessions_updated_at", "updated_at"),)

    id = db.Column(db.Uuid, primary_key=True, default=uuid.uuid4)
    agent_id = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    # What the visitor was looking at when the conversation started, e.g. {"page": "/contact"}.
    context = db.Column(db.JSON)
    # Kept on the row so a conversation list needs no per-session queries.
    message_count = db.Column(db.Integer, nullable=False, default=0)
    preview = db.Column(db.String(200))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    messages = db.relationship(
        "AiMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="AiMessage.sequence",
    )


class AiMessage(db.Model):
    __tablename__ = "ai_messages"
    __table_args__ = (
        db.CheckConstraint(_in("role", ["user", "assistant"]), name="ck_ai_messages_role"),
        db.UniqueConstraint("session_id", "sequence", name="uq_ai_messages_session_sequence"),
        db.Index("ix_ai_messages_created_at", "created_at"),
        db.Index("ix_ai_messages_client_ip_hash_created_at", "client_ip_hash", "created_at"),
    )

    id = db.Column(db.Uuid, primary_key=True, default=uuid.uuid4)
    session_id = db.Column(db.Uuid, db.ForeignKey("ai_sessions.id", ondelete="CASCADE"), nullable=False)
    # 1, 2, 3 ... within the session: a question is always followed by its answer.
    sequence = db.Column(db.Integer, nullable=False, default=0)
    role = db.Column(db.String(10), nullable=False)
    content = db.Column(db.Text, nullable=False)
    # [{"type": "document" | "policy", "id": "...", "label": "..."}]: what the answer came from.
    citations = db.Column(db.JSON)
    # What the agent reported for the answer, for cost tracking.
    model = db.Column(db.String(100))
    input_tokens = db.Column(db.Integer)
    output_tokens = db.Column(db.Integer)
    tool_calls = db.Column(db.Integer)
    # The model's reasoning while it answered, as streamed by the agent (shown collapsed).
    reasoning = db.Column(db.Text)
    # Tools the agent used: [{"id", "name", "status", "input", "output"}], in call order.
    steps = db.Column(db.JSON)
    # The agent's to-do list for the answer, as it ended: [{"id", "content", "status"}].
    todos = db.Column(db.JSON)
    # Keyed hash of the sender's IP, on questions only: for the per-visitor rate limit; the
    # address itself is never stored.
    client_ip_hash = db.Column(db.String(64))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    session = db.relationship("AiSession", back_populates="messages")
