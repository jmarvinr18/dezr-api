import shutil
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from app.services.storage.base import MediaStorage, StorageError, StoredObject, StoredObjectNotFound


class LocalMediaStorage(MediaStorage):
    """Stores media on the local filesystem. For development and tests; use S3 in deployed
    environments so files survive container restarts and scale-out."""

    def __init__(self, root: str | Path):
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        relative = PurePosixPath(key)
        path = (self._root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(self._root):
            raise StorageError(f"Invalid storage key: {key}")
        return path

    def save(self, key: str, stream: BinaryIO, *, content_type: str, size: int) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("wb") as out:
                shutil.copyfileobj(stream, out)
        except OSError as exc:
            raise StorageError(f"Could not write {key}.") from exc
        return StoredObject(key=key, size=size, content_type=content_type)

    def open(self, key: str) -> BinaryIO:
        path = self._path(key)
        if not path.is_file():
            raise StoredObjectNotFound(key)
        return path.open("rb")

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
