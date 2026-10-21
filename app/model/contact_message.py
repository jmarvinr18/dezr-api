from app.database.db import db
from app.model.admin_user import utcnow
import uuid


class ContactMessage(db.Model):
    """An enquiry sent with the Contact page's form."""

    __tablename__ = "contact_messages"
    __table_args__ = (
        db.Index("ix_contact_messages_created_at", "created_at"),
        db.Index("ix_contact_messages_client_ip_hash_created_at", "client_ip_hash", "created_at"),
    )

    id = db.Column(db.Uuid, primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(320), nullable=False)
    subject = db.Column(db.String(300), nullable=False)
    message = db.Column(db.Text, nullable=False)
    # The visitor ticked "send me a copy".
    send_copy = db.Column(db.Boolean, nullable=False, default=False)
    # Keyed hash of the sender's IP, only for the per-visitor rate limit; the address itself
    # is never stored.
    client_ip_hash = db.Column(db.String(64))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
