from app.database.db import db
import uuid
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)


class AdminUser(db.Model):
    """Someone who may sign in to the admin area and change the site's content."""

    __tablename__ = "admin_users"

    id = db.Column(db.Uuid, primary_key=True, default=uuid.uuid4)
    # Stored lower case; the sign-in compares lower case.
    email = db.Column(db.String(255), nullable=False, unique=True)
    # werkzeug.security hash (scrypt).
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    # Part of every access token. Bumped on sign-out and password change, which revokes all
    # of the admin's tokens at once.
    session_version = db.Column(db.Integer, nullable=False, default=0)
    last_login_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class AdminLoginFailure(db.Model):
    """A failed sign-in, keyed "ip:<address>" and "user:<email>", for the lockout. Kept in the
    database (not in memory) so every API worker sees the same count."""

    __tablename__ = "admin_login_failures"
    __table_args__ = (db.Index("ix_admin_login_failures_key_failed_at", "key", "failed_at"),)

    id = db.Column(db.Uuid, primary_key=True, default=uuid.uuid4)
    key = db.Column(db.String(300), nullable=False)
    failed_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
