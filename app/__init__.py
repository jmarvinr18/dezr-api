from http import HTTPStatus

from flask import Flask, request
from flask_migrate import Migrate
from flask_smorest import Api
from sqlalchemy import text
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import Config
from app.database import db
from app.extensions.ai import init_ai
from app.extensions.auth import init_auth
from app.extensions.ingestion import init_ingestion
from app.extensions.storage import init_storage
from app.services.errors import ServiceError
from app import model  # noqa: F401  (registers models with SQLAlchemy / Alembic)

migrate = Migrate()


def create_app(db_url=None, config_overrides=None, media_storage=None, embedder=None, agent_client=None):

    app = Flask(__name__)
    app.config.from_object(Config)
    if db_url:
        app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config.update(config_overrides or {})

    # Behind the Nuxt server / a reverse proxy, take the visitor's IP from X-Forwarded-For
    # (sign-in lockout, contact form limit), but only from as many proxies as are really there.
    proxies = app.config["TRUSTED_PROXY_COUNT"]
    if proxies > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies, x_host=proxies)

    db.init_app(app)
    # S3 (or local disk in development) for uploaded media and documents.
    init_storage(app, media_storage)
    # Embedding model and background processing for the assistant's documents.
    init_ingestion(app, embedder)
    # The AI agent the chat endpoints relay to (dezr-ai on Bedrock AgentCore, or plain HTTP locally).
    init_ai(app, agent_client)
    # Signing key for admin access tokens.
    init_auth(app)
    # Schema changes go through migrations (flask db upgrade), not db.create_all().
    migrate.init_app(app=app, db=db)

    api_endpoints = Api(app)
    from app.routes import blueprints

    for blp in blueprints:
        api_endpoints.register_blueprint(blp)

    @app.errorhandler(ServiceError)
    def handle_service_error(error: ServiceError):
        # Same body shape as flask-smorest's own errors.
        body = {
            "code": error.status_code,
            "status": HTTPStatus(error.status_code).phrase,
            "message": error.message,
        }
        if error.errors:
            body["errors"] = error.errors
        return body, error.status_code, error.headers

    @app.after_request
    def add_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        origin = request.headers.get("Origin")
        if origin and origin in app.config["CORS_ORIGINS"]:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Headers"] = "Accept, Authorization, Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            response.headers["Access-Control-Expose-Headers"] = "Content-Disposition, Retry-After"
            response.headers["Vary"] = "Origin"
        return response

    @app.get("/healthz")
    def healthz():
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            app.logger.exception("Health check failed")
            return {"status": "error", "database": "unreachable"}, 503
        return {"status": "ok"}

    from app.cli import register_cli

    register_cli(app)

    return app
