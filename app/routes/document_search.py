import hmac

from flask import current_app, request
from flask.views import MethodView
from flask_smorest import Blueprint

from app.extensions.ingestion import get_document_search_service
from app.schema import DocumentSearchInputSchema, DocumentSearchResultSchema
from app.services.errors import NotConfigured, Unauthorized

# OpenAPI: "Authorization: Bearer <DOCUMENT_SEARCH_API_KEY>" (see Config.API_SPEC_OPTIONS).
SEARCH_KEY_SECURITY = [{"searchKey": []}]

blp = Blueprint(
    "document_search",
    __name__,
    url_prefix="/api/v1/documents",
    description="Search the documents' passages (for the AI assistant, with the search key)",
)


@blp.before_request
def require_search_key():
    """Checked before the body is validated, so callers without the key learn nothing else.
    CORS preflights carry no credentials and pass; the request that follows is checked."""
    if request.method == "OPTIONS":
        return
    expected = current_app.config.get("DOCUMENT_SEARCH_API_KEY")
    if not expected:
        raise NotConfigured("Document search is turned off: DOCUMENT_SEARCH_API_KEY is not set.")
    scheme, _, key = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(key.strip().encode(), expected.encode()):
        raise Unauthorized("A valid search key is required.")


@blp.after_request
def no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@blp.route("/search")
class DocumentSearch(MethodView):
    @blp.doc(security=SEARCH_KEY_SECURITY)
    @blp.arguments(DocumentSearchInputSchema)
    @blp.response(200, DocumentSearchResultSchema)
    @blp.alt_response(401, description="No or wrong search key")
    @blp.alt_response(503, description="Search is turned off (no DOCUMENT_SEARCH_API_KEY) or the database isn't PostgreSQL")
    def post(self, args):
        """Find the passages that best answer a query

        Hybrid search over the passages of `ready` documents: nearest vectors (embedded with the
        same model as the passages) and full-text keyword matches, merged with reciprocal rank
        fusion. `mode` is `keyword` when no vectors were available. Each passage carries its
        document's title, heading path and pages, for citing it.
        """
        return get_document_search_service().search(args["query"].strip(), args["limit"])
