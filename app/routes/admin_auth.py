from flask.views import MethodView
from flask_smorest import Blueprint

from app.extensions.auth import get_auth_service
from app.routes.admin_common import ADMIN_SECURITY, _no_store
from app.schema import AdminUserSchema, LoginInputSchema, LoginResultSchema
from app.services.client import client_ip
from app.services.current_admin import get_current_admin

# Not an admin_blueprint: signing in is the one admin endpoint open to anyone.
blp = Blueprint(
    "admin_auth",
    __name__,
    url_prefix="/api/v1/admin/auth",
    description="Admin sign-in. Send the token as \"Authorization: Bearer <token>\" to /api/v1/admin/...",
)
blp.after_request(_no_store)


@blp.route("/login")
class Login(MethodView):
    @blp.arguments(LoginInputSchema)
    @blp.response(200, LoginResultSchema)
    @blp.alt_response(401, description="Incorrect email or password")
    @blp.alt_response(429, description="Too many failed attempts from this IP or for this account")
    @blp.alt_response(503, description="SECRET_KEY is not set on the server")
    def post(self, data):
        """Sign in with email and password

        Returns an access token valid for ADMIN_SESSION_HOURS. After ADMIN_MAX_FAILURES wrong
        passwords within ADMIN_LOCKOUT_MINUTES, the IP and the account are locked out for the
        rest of that window (429 with Retry-After).
        """
        return get_auth_service().login(data["email"], data["password"], client_ip())


@blp.route("/logout")
class Logout(MethodView):
    @blp.doc(security=ADMIN_SECURITY)
    @blp.response(204)
    def post(self):
        """Sign out everywhere (revokes all of this admin's tokens)"""
        get_auth_service().logout(get_current_admin())


@blp.route("/me")
class Me(MethodView):
    @blp.doc(security=ADMIN_SECURITY)
    @blp.response(200, AdminUserSchema)
    def get(self):
        """The signed-in admin"""
        return get_current_admin()
