from flask.views import MethodView
from flask_smorest import Blueprint

from app.schema import PageByPathQuerySchema, PageSchema, PageSummarySchema
from app.services import page_service

blp = Blueprint(
    "pages",
    __name__,
    url_prefix="/api/v1/pages",
    description="Published content pages (home, partners, library, language pages, contact, ...)",
)


@blp.route("")
class PageList(MethodView):
    @blp.response(200, PageSummarySchema(many=True))
    def get(self):
        """List published pages in site-map order"""
        return page_service.list_published()


@blp.route("/by-path")
class PageByPath(MethodView):
    @blp.arguments(PageByPathQuerySchema, location="query")
    @blp.response(200, PageSchema)
    def get(self, args):
        """Get a published page by its path (?path=/ for the home page)

        The body is Markdown with the site's components. It is returned as written: render it
        with a sanitising renderer (dezr-ui's renderMarkdown()).
        """
        return page_service.get_published(args["path"])
