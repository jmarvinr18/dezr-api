from marshmallow import Schema, fields, validate

from app.schema.fields import UtcDateTime


class DocumentSchema(Schema):
    """A document the AI assistant can search (same fields as IngestedDocument in dezr-ui)."""

    id = fields.UUID()
    title = fields.Str()
    file_name = fields.Str(data_key="fileName")
    content_type = fields.Str(data_key="contentType")
    size = fields.Int(attribute="size_bytes")
    status = fields.Str(metadata={"description": "processing | ready | failed"})
    chunk_count = fields.Int(data_key="chunkCount", allow_none=True, metadata={"description": "Passages, once ready"})
    error = fields.Str(allow_none=True, metadata={"description": "Why processing failed"})
    uploaded_at = UtcDateTime(data_key="uploadedAt")
    uploaded_by = fields.Str(data_key="uploadedBy", allow_none=True)
    processed_at = UtcDateTime(data_key="processedAt", allow_none=True)


class DocumentUploadCreateSchema(Schema):
    size = fields.Int(required=True, metadata={"description": "The file's size in bytes (up to 25 MB)"})


class DocumentUploadSchema(Schema):
    """Where the browser sends the file: a form POST to `url` with every entry of `fields`, then
    the file as the last part, named `file`. S3 answers 204."""

    upload_id = fields.UUID(data_key="uploadId", metadata={"description": "Confirm with POST /uploads/{uploadId}/complete"})
    url = fields.Str()
    fields_ = fields.Dict(keys=fields.Str(), values=fields.Str(), attribute="fields", data_key="fields")
    expires_at = UtcDateTime(data_key="expiresAt", metadata={"description": "Send the file before this"})
    max_size = fields.Int(data_key="maxSize")


class DocumentUploadCompleteSchema(Schema):
    file_name = fields.Str(
        data_key="fileName",
        load_default=None,
        validate=validate.Length(max=255),
        metadata={"description": "The file's own name (shown in the list; the type is judged from its content)"},
    )
    title = fields.Str(load_default=None, allow_none=True, metadata={"description": "Up to 200 characters; defaults to the file name"})


class DocumentChunkSchema(Schema):
    index = fields.Int(attribute="chunk_index")
    content = fields.Str()
    heading_path = fields.List(fields.Str(), data_key="headingPath")
    page_start = fields.Int(data_key="pageStart", allow_none=True)
    page_end = fields.Int(data_key="pageEnd", allow_none=True)
    token_count = fields.Int(data_key="tokenCount", metadata={"description": "Approximate"})
    block_types = fields.List(fields.Str(), data_key="blockTypes")
    has_embedding = fields.Function(lambda chunk: chunk.embedding is not None, data_key="hasEmbedding")


class DocumentChunkListSchema(Schema):
    items = fields.List(fields.Nested(DocumentChunkSchema))
    total = fields.Int()
    page = fields.Int()
    page_size = fields.Int(data_key="pageSize")
    page_count = fields.Int(data_key="pageCount")


class DocumentChunkQuerySchema(Schema):
    page = fields.Int(load_default=1, validate=validate.Range(min=1))
    page_size = fields.Int(data_key="pageSize", load_default=50, validate=validate.Range(min=1, max=200))
