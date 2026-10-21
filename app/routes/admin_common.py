from flask_smorest import Blueprint

from app.services.current_admin import require_admin

# OpenAPI: the admin endpoints take "Authorization: Bearer <token>" (see Config.API_SPEC_OPTIONS).
ADMIN_SECURITY = [{"adminToken": []}]


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def admin_blueprint(name: str, import_name: str, *, url_prefix: str, description: str) -> Blueprint:
    """A blueprint whose every endpoint needs a signed-in admin. The check runs before any view,
    so an endpoint can't be left open by forgetting it."""
    blp = Blueprint(name, import_name, url_prefix=url_prefix, description=description)
    blp.before_request(require_admin)
    blp.after_request(_no_store)
    return blp
