from marshmallow import Schema, fields, validate

from app.constants import SEARCH_QUERY_MAX_LENGTH


class SearchQuerySchema(Schema):
    q = fields.Str(load_default="", validate=validate.Length(max=SEARCH_QUERY_MAX_LENGTH))


class SearchResultSchema(Schema):
    """One section of a page (same shape as @nuxt/content's queryCollectionSearchSections)."""

    id = fields.Str(metadata={"description": "Page path, plus #heading-anchor for a section"})
    title = fields.Str()
    titles = fields.List(fields.Str(), metadata={"description": "Page title and parent headings"})
    level = fields.Int()
    content = fields.Str(metadata={"description": "The section's plain text"})
