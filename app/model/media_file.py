from app.database.db import db
from app.model.admin_user import utcnow
import uuid


class MediaFile(db.Model):
    """An image or PDF uploaded in the admin area (partner photos, page images, the PDFs
    behind the download banners). The bytes live in S3 / on disk; this is the index."""

    __tablename__ = "media_files"

    id = db.Column(db.Uuid, primary_key=True, default=uuid.uuid4)
    # Public, URL-safe name ("partner-photo.jpg"), derived from the uploaded file name.
    name = db.Column(db.String(80), nullable=False, unique=True)
    content_type = db.Column(db.String(100), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    # Random storage key; the public name never becomes part of it.
    storage_key = db.Column(db.String(500), nullable=False)
    uploaded_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    uploaded_by = db.Column(db.String(255), nullable=False)
