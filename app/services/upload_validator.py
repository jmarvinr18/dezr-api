import os
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO

from app.constants import MEDIA_MAX_FILE_SIZE
from app.services.errors import ValidationFailed


@dataclass(frozen=True)
class FileType:
    extension: str
    content_type: str
    # True when the file's leading bytes are a genuine file of this type.
    matches: Callable[[bytes], bool]


# Accepted formats, recognised by their first bytes (never by the name alone). The extension
# and content type we store come from here, never from the client. SVG and HTML are left out
# on purpose: they can carry scripts.
ACCEPTED_FILE_TYPES: tuple[FileType, ...] = (
    FileType("png", "image/png", lambda b: b.startswith(b"\x89PNG\r\n\x1a\n")),
    FileType("jpg", "image/jpeg", lambda b: b.startswith(b"\xff\xd8\xff")),
    FileType("gif", "image/gif", lambda b: b.startswith(b"GIF8")),
    FileType("webp", "image/webp", lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP"),
    FileType("pdf", "application/pdf", lambda b: b.startswith(b"%PDF-")),
)
_SNIFF_BYTES = 12


@dataclass(frozen=True)
class IncomingFile:
    """An uploaded file, independent of the web framework that received it."""

    filename: str
    stream: BinaryIO


@dataclass(frozen=True)
class ValidatedUpload:
    # URL-safe name without extension, e.g. "partner-photo-1".
    base_name: str
    extension: str
    size: int
    content_type: str
    stream: BinaryIO


def slugify_name(filename: str) -> str:
    """'C:\\photos\\Partner Photo (1).JPG' → 'partner-photo-1' (same as dezr-ui's slugifyName)."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    stem = re.sub(r"\.[^.]*$", "", name)
    stem = "".join(c for c in unicodedata.normalize("NFKD", stem) if not unicodedata.combining(c))
    stem = re.sub(r"[^a-z0-9]+", "-", stem.lower())[:60].strip("-")
    return stem or "file"


def _stream_size(stream: BinaryIO) -> int:
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    return size


class UploadValidator:
    """Checks a media upload against the site's rules. It only validates; it doesn't store anything."""

    def __init__(
        self,
        file_types: tuple[FileType, ...] = ACCEPTED_FILE_TYPES,
        max_file_size: int = MEDIA_MAX_FILE_SIZE,
    ):
        self._file_types = file_types
        self._max_file_size = max_file_size

    def validate(self, incoming: IncomingFile | None) -> ValidatedUpload:
        """Returns the upload ready to store, or raises ValidationFailed."""
        if incoming is None or not incoming.filename:
            raise ValidationFailed("Choose a file to upload in the 'file' field.")

        size = _stream_size(incoming.stream)
        max_mb = self._max_file_size // (1024 * 1024)
        if size == 0:
            raise ValidationFailed("The file is empty.", errors={"file": "The file is empty."})
        if size > self._max_file_size:
            raise ValidationFailed(f"Files can be up to {max_mb} MB.", errors={"file": f"Files can be up to {max_mb} MB."})

        head = incoming.stream.read(_SNIFF_BYTES)
        incoming.stream.seek(0)
        file_type = next((t for t in self._file_types if t.matches(head)), None)
        if file_type is None:
            message = "Upload a PNG, JPG, GIF, WebP image or a PDF."
            raise ValidationFailed(message, errors={"file": message})

        return ValidatedUpload(
            base_name=slugify_name(incoming.filename),
            extension=file_type.extension,
            size=size,
            content_type=file_type.content_type,
            stream=incoming.stream,
        )
