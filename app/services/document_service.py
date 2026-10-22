"""Documents the AI assistant can search: upload, list, read and delete. Processing (chunking and
embedding) happens later, in services/ingestion_service.py.

A file reaches the store one of two ways:
- upload(): the file comes through this API (multipart). Simple, but every byte passes through
  dezr-ui's server, and Amplify Hosting refuses request bodies over about 4.5 MB.
- start_upload() then complete_upload(): the browser sends the file straight to S3 with a
  presigned form, under incoming/<upload id>; confirming it runs the same checks, moves it to
  documents/ and records it. Until then nothing is in the database, so no processor can claim
  a document whose file isn't there yet, and an abandoned upload is just an object S3 expires.
"""

import logging
import math
import re
import tempfile
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import BinaryIO

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.constants import DOCUMENT_TITLE_MAX_LENGTH, DOCUMENT_UPLOAD_EXPIRES_SECONDS, DOCUMENT_UPLOAD_PREFIX
from app.model import Document, DocumentChunk
from app.model.admin_user import utcnow
from app.services.document_validator import DocumentValidator, ValidatedDocument
from app.services.errors import NotFound, NotImplementedHere, ServiceError, StorageUnavailable, ValidationFailed
from app.services.storage import MediaStorage, StorageError, StoredObjectNotFound
from app.services.upload_validator import IncomingFile

log = logging.getLogger(__name__)

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_STORE_DOWN = "The file store is unavailable. Please try again."


def default_document_key(extension: str) -> str:
    # Random object names: the uploaded file name never becomes part of the key.
    return f"documents/{uuid.uuid4().hex}.{extension}"


def upload_key(upload_id: uuid.UUID) -> str:
    """Where a direct upload waits until it is confirmed."""
    return f"{DOCUMENT_UPLOAD_PREFIX}/{upload_id}"


class DocumentService:
    def __init__(
        self,
        session: Session,
        storage: MediaStorage,
        validator: DocumentValidator,
        on_uploaded: Callable[[], None] | None = None,
        key_factory: Callable[[str], str] = default_document_key,
        knowledge_base=None,
    ):
        self._session = session
        self._storage = storage
        self._validator = validator
        self._on_uploaded = on_uploaded
        self._key_factory = key_factory
        # RETRIEVAL_BACKEND=bedrock_kb: deleted documents leave the knowledge base too.
        self._knowledge_base = knowledge_base

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
        return self._store(self._validator.validate(incoming), title, uploaded_by)

    def start_upload(self, size: int) -> dict:
        """A presigned form for sending a file of `size` bytes straight to S3. Nothing is recorded
        until complete_upload() confirms it."""
        self._validator.check_size(size)
        upload_id = uuid.uuid4()
        max_size = self._validator.max_file_size
        try:
            form = self._storage.presign_upload(
                upload_key(upload_id), max_size=max_size, expires_in=DOCUMENT_UPLOAD_EXPIRES_SECONDS
            )
        except StorageError as exc:
            raise StorageUnavailable(_STORE_DOWN) from exc
        if form is None:
            raise NotImplementedHere(
                "Direct uploads need S3 storage. Send the file to POST /api/v1/admin/documents instead."
            )
        return {
            "upload_id": upload_id,
            "url": form.url,
            "fields": form.fields,
            "expires_at": utcnow() + timedelta(seconds=DOCUMENT_UPLOAD_EXPIRES_SECONDS),
            "max_size": max_size,
        }

    def complete_upload(
        self, upload_id: uuid.UUID, file_name: str | None, title: str | None, uploaded_by: str
    ) -> Document:
        """Records a file sent with start_upload()'s form as `processing`, once it passes the same
        checks as upload(): the kind is judged from its bytes, never from the name. A file that
        fails them is deleted; a missing one (never sent, or expired) is 404."""
        # Checked before the file is read, so a mistake here leaves it in place for a retry.
        title = self._clean_title(title)
        if not (file_name or "").strip():
            message = "Send the uploaded file's name as fileName."
            raise ValidationFailed(message, errors={"fileName": message})

        staged = upload_key(upload_id)
        with self._download(staged) as buffer:
            try:
                upload = self._validator.validate(IncomingFile(file_name, buffer))
            except ServiceError:
                self._discard(staged)
                raise
            document = self._store(upload, title, uploaded_by)
        self._discard(staged)
        return document

    def _store(self, upload: ValidatedDocument, title: str, uploaded_by: str) -> Document:
        """Saves a validated file under a new key and records it as `processing`."""
        key = self._key_factory(upload.kind)
        try:
            self._storage.save(key, upload.stream, content_type=upload.content_type, size=upload.size)
        except StorageError as exc:
            raise StorageUnavailable(_STORE_DOWN) from exc

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
        key, document_id, kind = document.storage_key, document.id, document.kind
        self._session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
        self._session.delete(document)
        self._session.commit()
        self._discard(key)
        if self._knowledge_base is not None:
            self._knowledge_base.remove(document_id, kind)

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

    def _download(self, key: str) -> BinaryIO:
        """The object's bytes in a seekable buffer (the validator needs to seek), reading at most
        one byte past the size limit, so an oversized object fails the size check without being
        read whole."""
        limit = self._validator.max_file_size
        try:
            source = self._storage.open(key)
        except StoredObjectNotFound as exc:
            raise NotFound("The uploaded file wasn't found. It may have expired; upload it again.") from exc
        except StorageError as exc:
            raise StorageUnavailable(_STORE_DOWN) from exc

        buffer = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
        try:
            while buffer.tell() <= limit:
                chunk = source.read(min(1024 * 1024, limit + 1 - buffer.tell()))
                if not chunk:
                    break
                buffer.write(chunk)
        except Exception as exc:
            # A connection that drops mid-read (botocore raises several kinds of error for it).
            buffer.close()
            log.exception("Reading %s from storage failed", key)
            raise StorageUnavailable(_STORE_DOWN) from exc
        finally:
            source.close()
        buffer.seek(0)
        return buffer

    def _discard(self, key: str) -> None:
        # Best effort: the database is already correct, so a failure only leaves an orphan behind.
        try:
            self._storage.delete(key)
        except StorageError:
            log.warning("Orphaned document object left in storage: %s", key)
