from flask.views import MethodView

from app.routes.admin_common import ADMIN_SECURITY, admin_blueprint
from app.schema import (
    AdminPageDetailSchema,
    AdminPageListItemSchema,
    AdminPageSchema,
    PageCreateSchema,
    PageUpdateSchema,
)
from app.services import page_service
from app.services.current_admin import get_current_admin

blp = admin_blueprint(
    "admin_pages",
    __name__,
    url_prefix="/api/v1/admin/pages",
    description="Admin > Pages: create, edit, unpublish, delete or reset content pages",
)


class _Detail:
    """A page plus its shipped version (`default`), for AdminPageDetailSchema to dump."""

    def __init__(self, page, default):
        self._page = page
        self.default = default

    def __getattr__(self, name):
        return getattr(self._page, name)


def detail(page):
    return _Detail(page, page_service.default_for(page))


@blp.route("")
class AdminPageList(MethodView):
    @blp.doc(security=ADMIN_SECURITY)
    @blp.response(200, AdminPageListItemSchema(many=True))
    def get(self):
        """List every page, published or not, with whether it was edited"""
        return page_service.list_pages()

    @blp.doc(security=ADMIN_SECURITY)
    @blp.arguments(PageCreateSchema)
    @blp.response(201, AdminPageSchema)
    @blp.alt_response(409, description="Another page already uses this path")
    def post(self, data):
        """Create a page"""
        return page_service.create_page(data, get_current_admin().email)


@blp.route("/<uuid:page_id>")
class AdminPageDetail(MethodView):
    @blp.doc(security=ADMIN_SECURITY)
    @blp.response(200, AdminPageDetailSchema)
    def get(self, page_id):
        """Get a page with its shipped version (`default`)"""
        return detail(page_service.get_page(page_id))

    @blp.doc(security=ADMIN_SECURITY)
    @blp.arguments(PageUpdateSchema)
    @blp.response(200, AdminPageDetailSchema)
    @blp.alt_response(409, description="The path is taken, or the home page would be moved or unpublished")
    def patch(self, data, page_id):
        """Change a page (only the fields sent)

        The change is live on the website as soon as this returns.
        """
        page = page_service.get_page(page_id)
        return detail(page_service.update_page(page, data, get_current_admin().email))

    @blp.doc(security=ADMIN_SECURITY)
    @blp.response(204)
    @blp.alt_response(409, description="The home page can't be deleted")
    def delete(self, page_id):
        """Delete a page"""
        page_service.delete_page(page_service.get_page(page_id))


@blp.route("/<uuid:page_id>/reset")
class AdminPageReset(MethodView):
    @blp.doc(security=ADMIN_SECURITY)
    @blp.response(200, AdminPageDetailSchema)
    @blp.alt_response(409, description="The page was created by an admin and has no shipped version")
    def post(self, page_id):
        """Restore the shipped version of a page (text, options and path)"""
        return detail(page_service.reset_page(page_service.get_page(page_id)))
