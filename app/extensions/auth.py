import logging
import secrets

from flask import Flask, current_app

from app.database import db
from app.services.auth_service import AuthService

log = logging.getLogger(__name__)


def init_auth(app: Flask) -> None:
    """Makes sure admin tokens can be signed. Without SECRET_KEY, debug and test runs get a
    random key per process (tokens end with a restart); production answers 503 on sign-in."""
    if app.config.get("SECRET_KEY"):
        return
    if app.debug or app.testing:
        app.config["SECRET_KEY"] = secrets.token_hex(32)
    else:
        log.warning("SECRET_KEY is not set: admin sign-in is disabled until it is.")


def get_auth_service() -> AuthService:
    config = current_app.config
    return AuthService(
        db.session,
        config.get("SECRET_KEY"),
        session_hours=config["ADMIN_SESSION_HOURS"],
        max_failures=config["ADMIN_MAX_FAILURES"],
        lockout_minutes=config["ADMIN_LOCKOUT_MINUTES"],
    )
