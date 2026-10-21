from flask import Flask, current_app

from app.database import db
from app.services.media_service import MediaService
from app.services.storage import MediaStorage, build_media_storage
from app.services.upload_validator import UploadValidator

_EXTENSION_KEY = "media_storage"


def init_storage(app: Flask, storage: MediaStorage | None = None) -> None:
    """Creates the media storage once per app. Pass one to override it (tests)."""
    app.extensions[_EXTENSION_KEY] = storage or build_media_storage(app.config)


def get_media_storage() -> MediaStorage:
    return current_app.extensions[_EXTENSION_KEY]


def get_media_service() -> MediaService:
    return MediaService(db.session, get_media_storage(), UploadValidator())
