from marshmallow import Schema, fields, validate

from app.constants import DOCUMENT_SEARCH_MAX_RESULTS, DOCUMENT_SEARCH_QUERY_MAX_LENGTH


class DocumentSearchInputSchema(Schema):
    query = fields.Str(
        required=True,
        validate=[
            validate.Length(max=DOCUMENT_SEARCH_QUERY_MAX_LENGTH),
            validate.Regexp(r"\S", error="Enter something to search for."),
        ],
        metadata={"description": "The question or search terms, as the assistant would ask them"},
    )
    limit = fields.Int(
        load_default=5,
        validate=validate.Range(min=1, max=DOCUMENT_SEARCH_MAX_RESULTS),
        metadata={"description": f"Passages to return, 1 to {DOCUMENT_SEARCH_MAX_RESULTS}"},
    )


class DocumentPassageSchema(Schema):
    """One passage of a ready document, with what's needed to cite it."""

    document_id = fields.UUID(data_key="documentId")
    chunk_id = fields.UUID(
        data_key="chunkId", attribute="id", allow_none=True, metadata={"description": "null for knowledge base results"}
    )
    title = fields.Str(metadata={"description": "The document's title"})
    file_name = fields.Str(data_key="fileName")
    heading_path = fields.List(fields.Str(), data_key="headingPath", metadata={"description": "Outermost first"})
    page_start = fields.Int(data_key="pageStart", allow_none=True, metadata={"description": "PDF only"})
    page_end = fields.Int(data_key="pageEnd", allow_none=True)
    content = fields.Str()
    score = fields.Float(
        allow_none=True,
        metadata={"description": "Higher is better: reciprocal rank fusion, or the knowledge base's relevance"},
    )


class DocumentSearchResultSchema(Schema):
    mode = fields.Str(metadata={"description": "hybrid (vectors and keywords) | keyword (no vectors available) | knowledge_base (Bedrock Knowledge Base)"})
    items = fields.List(fields.Nested(DocumentPassageSchema))
