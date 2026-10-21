from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import BinaryIO


class StorageError(Exception):
    """A storage backend failed (network, permissions, missing bucket, ...)."""


class StoredObjectNotFound(StorageError):
    """No object exists under the requested key."""


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    content_type: str


class MediaStorage(ABC):
    """Where uploaded media bytes live. Keys are backend-neutral, '/'-separated paths such as
    'media/<random>.pdf'; the database stores only the key."""

    @abstractmethod
    def save(self, key: str, stream: BinaryIO, *, content_type: str, size: int) -> StoredObject:
        """Store the stream under key, replacing anything already there."""

    @abstractmethod
    def open(self, key: str) -> BinaryIO:
        """A readable stream of the object's bytes. Raises StoredObjectNotFound."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove the object. Deleting a missing key is not an error."""

    def delete_many(self, keys: list[str]) -> None:
        for key in keys:
            self.delete(key)

    def exists(self, key: str) -> bool:
        try:
            self.open(key).close()
        except StoredObjectNotFound:
            return False
        return True
