from flask.views import MethodView

from app.routes.admin_common import ADMIN_SECURITY, admin_blueprint
from app.schema import AdminSiteSettingsSchema, SiteSettingsPatchSchema, SiteSettingsSchema
from app.services import site_settings_service
from app.services.current_admin import get_current_admin

blp = admin_blueprint(
    "admin_site_settings",
    __name__,
    url_prefix="/api/v1/admin/site-settings",
    description="Admin > Site settings: change or reset firm details, menus, slideshow, banners, ...",
)


@blp.route("")
class AdminSiteSettings(MethodView):
    @blp.doc(security=ADMIN_SECURITY)
    @blp.response(200, AdminSiteSettingsSchema)
    def get(self):
        """Current settings, the shipped defaults and the latest change"""
        return site_settings_service.admin_view()

    @blp.doc(security=ADMIN_SECURITY)
    @blp.arguments(SiteSettingsSchema)
    @blp.response(200, AdminSiteSettingsSchema)
    def put(self, data):
        """Replace every section

        The change is live on the website as soon as this returns.
        """
        return site_settings_service.save_sections(data, get_current_admin().email)

    @blp.doc(security=ADMIN_SECURITY)
    @blp.arguments(SiteSettingsPatchSchema)
    @blp.response(200, AdminSiteSettingsSchema)
    def patch(self, data):
        """Replace only the sections sent

        Each section sent is replaced whole (a list sent with fewer items really is shorter).
        """
        return site_settings_service.save_sections(data, get_current_admin().email)

    @blp.doc(security=ADMIN_SECURITY)
    @blp.response(200, AdminSiteSettingsSchema)
    def delete(self):
        """Restore the shipped settings (every section)"""
        return site_settings_service.reset()


@blp.route("/<string:section>")
class AdminSiteSettingsSection(MethodView):
    @blp.doc(security=ADMIN_SECURITY)
    @blp.response(200, AdminSiteSettingsSchema)
    def delete(self, section):
        """Restore one section's shipped settings (site, contact, nav, slides, mission, banners, footer, assistant)"""
        return site_settings_service.reset([section])
