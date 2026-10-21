import logging
import uuid
from collections.abc import Callable
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.constants import MEDIA_NAME_PATTERN
from app.model import MediaFile
from app.services.errors import Conflict, NotFound, StorageUnavailable
from app.services.storage import MediaStorage, StorageError, StoredObjectNotFound
from app.services.upload_validator import IncomingFile, UploadValidator

log = logging.getLogger(__name__)


def default_media_key(extension: str) -> str:
    # Random object names: the public file name never becomes part of the key.
    return f"media/{uuid.uuid4().hex}.{extension}"


class MediaService:
    """Images and PDFs uploaded in the admin area: upload, list, read and delete.

    Depends on the MediaStorage abstraction (S3 in production, local disk in development) and
    an UploadValidator. Neither is created here, so either can be swapped or mocked.
    """

    def __init__(
        self,
        session: Session,
        storage: MediaStorage,
        validator: UploadValidator,
        key_factory: Callable[[str], str] = default_media_key,
    ):
        self._session = session
        self._storage = storage
        self._validator = validator
        self._key_factory = key_factory

    def list_files(self) -> list[MediaFile]:
        return list(self._session.scalars(select(MediaFile).order_by(MediaFile.uploaded_at.desc())))

    def get(self, name: str) -> MediaFile:
        media = None
        if MEDIA_NAME_PATTERN.match(name):
            media = self._session.scalar(select(MediaFile).where(MediaFile.name == name))
        if media is None:
            raise NotFound("File not found.")
        return media

    def upload(self, incoming: IncomingFile | None, uploaded_by: str) -> MediaFile:
        """Validates the file, stores it, then records it. If saving the record fails, the
        stored object is removed again."""
        upload = self._validator.validate(incoming)
        name = self._free_name(upload.base_name, upload.extension)
        key = self._key_factory(upload.extension)

        try:
            self._storage.save(key, upload.stream, content_type=upload.content_type, size=upload.size)
        except StorageError as exc:
            raise StorageUnavailable("The media store is unavailable. Please try again.") from exc

        media = MediaFile(
            name=name,
            content_type=upload.content_type,
            size_bytes=upload.size,
            storage_key=key,
            uploaded_by=uploaded_by,
        )
        self._session.add(media)
        try:
            self._session.commit()
        except Exception as exc:
            self._session.rollback()
            self._discard([key])
            if isinstance(exc, IntegrityError):
                # Another upload took the same name between the check and the insert.
                raise Conflict("A file with this name was uploaded at the same time. Please try again.") from exc
            raise
        return media

    def remove(self, media: MediaFile) -> None:
        key = media.storage_key
        self._session.delete(media)
        self._session.commit()
        self._discard([key])

    def open(self, media: MediaFile) -> BinaryIO:
        try:
            return self._storage.open(media.storage_key)
        except StoredObjectNotFound as exc:
            raise NotFound("File not found.") from exc
        except StorageError as exc:
            raise StorageUnavailable("The media store is unavailable. Please try again.") from exc

    def _free_name(self, base: str, extension: str) -> str:
        """"photo.jpg", or "photo-2.jpg", "photo-3.jpg", ... when that is taken."""
        taken = set(
            self._session.scalars(select(MediaFile.name).where(MediaFile.name.startswith(base, autoescape=True)))
        )
        name, n = f"{base}.{extension}", 2
        while name in taken:
            name, n = f"{base}-{n}.{extension}", n + 1
        return name

    def _discard(self, keys: list[str]) -> None:
        # Best effort: the database is already correct, so a failure here only leaves an
        # orphaned object behind. Log it for cleanup rather than failing the request.
        try:
            self._storage.delete_many(keys)
        except StorageError:
            log.warning("Orphaned media objects left in storage: %s", keys)
