"""Documents the AI assistant can search: upload, list, read and delete. Processing (chunking and
embedding) happens later, in services/ingestion_service.py."""

import logging
import math
import re
import uuid
from collections.abc import Callable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.constants import DOCUMENT_TITLE_MAX_LENGTH
from app.model import Document, DocumentChunk
from app.services.document_validator import DocumentValidator
from app.services.errors import NotFound, StorageUnavailable, ValidationFailed
from app.services.storage import MediaStorage, StorageError
from app.services.upload_validator import IncomingFile

log = logging.getLogger(__name__)

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def default_document_key(extension: str) -> str:
    # Random object names: the uploaded file name never becomes part of the key.
    return f"documents/{uuid.uuid4().hex}.{extension}"


class DocumentService:
    def __init__(
        self,
        session: Session,
        storage: MediaStorage,
        validator: DocumentValidator,
        on_uploaded: Callable[[], None] | None = None,
        key_factory: Callable[[str], str] = default_document_key,
    ):
        self._session = session
        self._storage = storage
        self._validator = validator
        self._on_uploaded = on_uploaded
        self._key_factory = key_factory

    def list_documents(self) -> list[Document]:
        return list(self._session.scalars(select(Document).order_by(Document.uploaded_at.desc())))

    def get(self, document_id: uuid.UUID) -> Document:
        document = self._session.get(Document, document_id)
        if document is None:
            raise NotFound("Document not found.")
        return document

    def upload(self, incoming: IncomingFile | None, title: str | None, uploaded_by: str) -> Document:
        """Stores the file and records it as `processing`; chunking runs in the background."""
        title = self._clean_title(title)
        upload = self._validator.validate(incoming)
        key = self._key_factory(upload.kind)
        try:
            self._storage.save(key, upload.stream, content_type=upload.content_type, size=upload.size)
        except StorageError as exc:
            raise StorageUnavailable("The file store is unavailable. Please try again.") from exc

        document = Document(
            title=title or self._title_from(upload.file_name),
            file_name=upload.file_name,
            kind=upload.kind,
            content_type=upload.content_type,
            size_bytes=upload.size,
            storage_key=key,
            status="processing",
            attempts=0,
            uploaded_by=uploaded_by,
        )
        self._session.add(document)
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            self._discard(key)
            raise
        # Load the saved row now, so the response shows the upload as it was stored
        # ("processing"), even if a background processor finishes before it is sent.
        self._session.refresh(document)
        if self._on_uploaded:
            self._on_uploaded()
        return document

    def delete(self, document: Document) -> None:
        """Removes the file, its passages and their vectors. A document still processing is
        cancelled: the processor finds it gone and drops its work."""
        key = document.storage_key
        self._session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
        self._session.delete(document)
        self._session.commit()
        self._discard(key)

    def chunks(self, document: Document, page: int, page_size: int) -> dict:
        total = self._session.scalar(
            select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_id == document.id)
        )
        page_count = max(1, math.ceil(total / page_size))
        page = min(page, page_count)
        items = self._session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
            .order_by(DocumentChunk.chunk_index)
            .limit(page_size)
            .offset((page - 1) * page_size)
        ).all()
        return {"items": items, "total": total, "page": page, "page_size": page_size, "page_count": page_count}

    @staticmethod
    def _clean_title(title: str | None) -> str:
        title = " ".join(_CONTROL.sub(" ", title or "").split())
        if len(title) > DOCUMENT_TITLE_MAX_LENGTH:
            message = f"Use a title of up to {DOCUMENT_TITLE_MAX_LENGTH} characters."
            raise ValidationFailed("Some fields need attention.", errors={"title": message})
        return title

    @staticmethod
    def _title_from(file_name: str) -> str:
        """The file name without its extension (the spec's default title)."""
        stem = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
        return (" ".join(stem.split()) or file_name)[:DOCUMENT_TITLE_MAX_LENGTH]

    def _discard(self, key: str) -> None:
        # Best effort: the database is already correct, so a failure only leaves an orphan behind.
        try:
            self._storage.delete(key)
        except StorageError:
            log.warning("Orphaned document object left in storage: %s", key)
