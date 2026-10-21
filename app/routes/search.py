from flask.views import MethodView
from flask_smorest import Blueprint

from app.schema import SearchQuerySchema, SearchResultSchema
from app.services import search_service

blp = Blueprint(
    "search",
    __name__,
    url_prefix="/api/v1/search",
    description="Site search (the search box in the menu bar)",
)


@blp.route("")
class Search(MethodView):
    @blp.arguments(SearchQuerySchema, location="query")
    @blp.response(200, SearchResultSchema(many=True))
    def get(self, args):
        """Find page sections containing every word of ?q=

        Matches page titles, headings and text of published pages. Up to 50 results, in
        site-map order.
        """
        return search_service.search(args["q"])
