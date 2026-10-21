from flask import g, request

from app.extensions.auth import get_auth_service
from app.model import AdminUser
from app.services.errors import Unauthorized


def get_current_admin() -> AdminUser:
    """The admin making the request, from "Authorization: Bearer <token>" (the token from
    POST /api/v1/admin/auth/login). Raises Unauthorized without a valid one."""
    if "admin" not in g:
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise Unauthorized("Please sign in.")
        g.admin = get_auth_service().admin_for_token(token.strip())
    return g.admin


def require_admin() -> None:
    """before_request guard for the admin blueprints. CORS preflights carry no credentials,
    so they pass; the request that follows is checked."""
    if request.method != "OPTIONS":
        get_current_admin()
