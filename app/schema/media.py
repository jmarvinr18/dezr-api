from flask import current_app
from marshmallow import Schema, fields

from app.schema.fields import UtcDateTime


def media_url(name: str) -> str:
    """Where the website serves the file (MEDIA_PUBLIC_BASE_URL, "/media" by default)."""
    return f"{current_app.config['MEDIA_PUBLIC_BASE_URL']}/{name}"


class MediaFileSchema(Schema):
    """An uploaded image or PDF (same fields as MediaItem in dezr-ui)."""

    id = fields.UUID()
    name = fields.Str()
    url = fields.Function(lambda file: media_url(file.name), metadata={"description": "Link to use in pages and banners"})
    type = fields.Str(attribute="content_type")
    size = fields.Int(attribute="size_bytes")
    uploaded_at = UtcDateTime(data_key="uploadedAt")
    uploaded_by = fields.Str(data_key="uploadedBy")
