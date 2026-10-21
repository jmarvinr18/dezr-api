"""Rules for documents uploaded for the AI assistant: PDF, Word (.docx), plain text and Markdown,
up to 25 MB, recognised by their content (a renamed file is caught)."""

import os
import zipfile
from dataclasses import dataclass
from typing import BinaryIO

from app.constants import DOCUMENT_KINDS, DOCUMENT_MAX_FILE_SIZE
from app.services.errors import PayloadTooLarge, UnsupportedMediaType, ValidationFailed
from app.services.ingestion.text import looks_like_text
from app.services.upload_validator import IncomingFile

UNSUPPORTED = "Upload a PDF, Word (.docx), text (.txt) or Markdown (.md) file."
_TEXT_EXTENSIONS = {"txt": "txt", "text": "txt", "md": "md", "markdown": "md"}
# A .docx is a zip; refuse ones that would unpack to something huge (a "zip bomb").
_DOCX_MAX_UNPACKED = 250 * 1024 * 1024
_DOCX_MAX_ENTRIES = 5000


@dataclass(frozen=True)
class ValidatedDocument:
    file_name: str
    kind: str
    content_type: str
    size: int
    stream: BinaryIO


def _stream_size(stream: BinaryIO) -> int:
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    return size


class DocumentValidator:
    def __init__(self, max_file_size: int = DOCUMENT_MAX_FILE_SIZE):
        self._max_file_size = max_file_size

    @property
    def max_file_size(self) -> int:
        return self._max_file_size

    def check_size(self, size: int) -> None:
        """The size rule on its own, for a file the server hasn't received yet."""
        if size <= 0:
            raise ValidationFailed("The file is empty.", errors={"file": "The file is empty."})
        if size > self._max_file_size:
            raise PayloadTooLarge(f"Documents can be up to {self._max_file_size // (1024 * 1024)} MB.")

    def validate(self, incoming: IncomingFile | None) -> ValidatedDocument:
        if incoming is None or not incoming.filename:
            message = "Choose a document to upload in the 'file' field."
            raise ValidationFailed(message, errors={"file": message})

        # Browsers may send a full path; keep only the base name.
        name = incoming.filename.replace("\\", "/").rsplit("/", 1)[-1].strip()[:255] or "document"
        size = _stream_size(incoming.stream)
        self.check_size(size)

        kind = self._kind(name, incoming.stream)
        incoming.stream.seek(0)
        return ValidatedDocument(name, kind, DOCUMENT_KINDS[kind], size, incoming.stream)

    def _kind(self, name: str, stream: BinaryIO) -> str:
        head = stream.read(1024)
        stream.seek(0)
        if head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"%PDF-"):
            return "pdf"
        if head.startswith(b"PK\x03\x04"):
            if self._is_docx(stream):
                return "docx"
            raise UnsupportedMediaType(UNSUPPORTED)
        if head.startswith(b"\xd0\xcf\x11\xe0"):
            raise UnsupportedMediaType(
                "Older Word files (.doc) aren't supported. Save the document as .docx and upload it again."
            )
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if extension in _TEXT_EXTENSIONS and looks_like_text(stream.read(8192)):
            return _TEXT_EXTENSIONS[extension]
        raise UnsupportedMediaType(UNSUPPORTED)

    @staticmethod
    def _is_docx(stream: BinaryIO) -> bool:
        try:
            with zipfile.ZipFile(stream) as archive:
                entries = archive.infolist()
                names = {entry.filename for entry in entries}
                unpacked = sum(entry.file_size for entry in entries)
        except (zipfile.BadZipFile, OSError):
            return False
        finally:
            stream.seek(0)
        if len(entries) > _DOCX_MAX_ENTRIES or unpacked > _DOCX_MAX_UNPACKED:
            return False
        return "word/document.xml" in names and "[Content_Types].xml" in names
