from flask import request
from flask.views import MethodView

from app.constants import DOCUMENT_MAX_FILE_SIZE, DOCUMENT_TITLE_MAX_LENGTH
from app.extensions.ingestion import get_document_service, get_ingestion_service, get_runner
from app.routes.admin_common import ADMIN_SECURITY, admin_blueprint
from app.schema import DocumentChunkListSchema, DocumentChunkQuerySchema, DocumentSchema
from app.services.current_admin import get_current_admin
from app.services.upload_validator import IncomingFile

blp = admin_blueprint(
    "admin_documents",
    __name__,
    url_prefix="/api/v1/admin/documents",
    description="Admin > Documents: files the AI assistant can search (stored in S3, chunked and embedded)",
)


@blp.before_request
def allow_document_uploads():
    # Documents may be larger than the app-wide limit (MAX_CONTENT_LENGTH, sized for media).
    request.max_content_length = DOCUMENT_MAX_FILE_SIZE + 1024 * 1024


UPLOAD_BODY = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {
                    "file": {"type": "string", "format": "binary", "description": "PDF, .docx, .txt or .md, up to 25 MB"},
                    "title": {"type": "string", "maxLength": DOCUMENT_TITLE_MAX_LENGTH},
                },
            }
        }
    },
}


@blp.route("")
class AdminDocumentList(MethodView):
    @blp.doc(security=ADMIN_SECURITY)
    @blp.response(200, DocumentSchema(many=True))
    def get(self):
        """List documents, newest first

        Poll this while any document is `processing`.
        """
        if get_runner() is not None and get_ingestion_service().has_unclaimed():
            # Thread mode: pick up documents a restart interrupted.
            get_runner().kick()
        return get_document_service().list_documents()

    @blp.doc(security=ADMIN_SECURITY, requestBody=UPLOAD_BODY)
    @blp.response(202, DocumentSchema)
    @blp.alt_response(413, description="Larger than 25 MB")
    @blp.alt_response(415, description="Not a PDF, Word (.docx), text or Markdown file (judged by content)")
    @blp.alt_response(422, description="Title too long, or no file; `errors` names the field")
    @blp.alt_response(503, description="The file store (S3) is unavailable; nothing was stored")
    def post(self):
        """Upload a document for the assistant to search

        multipart/form-data with a `file` part and an optional `title` (up to 200 characters;
        defaults to the file name without its extension). The file is stored and the document
        returned straight away as `processing`; parsing, hybrid chunking and embedding run in
        the background, after which it is `ready` (with `chunkCount`) or `failed` (with `error`).
        """
        upload = request.files.get("file")
        incoming = IncomingFile(upload.filename, upload.stream) if upload and upload.filename else None
        return get_document_service().upload(incoming, request.form.get("title"), get_current_admin().email)


@blp.route("/<uuid:document_id>")
class AdminDocumentDetail(MethodView):
    @blp.doc(security=ADMIN_SECURITY)
    @blp.response(200, DocumentSchema)
    def get(self, document_id):
        """Get a document"""
        return get_document_service().get(document_id)

    @blp.doc(security=ADMIN_SECURITY)
    @blp.response(204)
    def delete(self, document_id):
        """Delete a document, its passages and their vectors

        Allowed in any status; a document still processing is cancelled.
        """
        documents = get_document_service()
        documents.delete(documents.get(document_id))


@blp.route("/<uuid:document_id>/chunks")
class AdminDocumentChunks(MethodView):
    @blp.doc(security=ADMIN_SECURITY)
    @blp.arguments(DocumentChunkQuerySchema, location="query")
    @blp.response(200, DocumentChunkListSchema)
    def get(self, args, document_id):
        """List a document's passages, in reading order

        Each passage has its heading path, page range, approximate token count and block types,
        for checking how a document was chunked.
        """
        documents = get_document_service()
        return documents.chunks(documents.get(document_id), args["page"], args["page_size"])
