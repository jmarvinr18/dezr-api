from flask.views import MethodView
from flask_smorest import Blueprint

from app.schema import SiteSettingsSchema
from app.services import site_settings_service

blp = Blueprint(
    "site_settings",
    __name__,
    url_prefix="/api/v1/site-settings",
    description="Firm details, menus, slideshow, right column, footer and assistant wording",
)


@blp.route("")
class SiteSettings(MethodView):
    @blp.response(200, SiteSettingsSchema)
    def get(self):
        """Everything the site layout shows

        Header (site), contact details, top menu (nav), slideshow (slides), mission box,
        download banners, footer columns and the AI assistant's wording. Sections an admin never
        changed come back as shipped.
        """
        return site_settings_service.current_settings()
