import logging
from typing import BinaryIO

from botocore.exceptions import BotoCoreError, ClientError

from app.services.storage.base import DirectUpload, MediaStorage, StorageError, StoredObject, StoredObjectNotFound

log = logging.getLogger(__name__)

_NOT_FOUND_CODES = {"NoSuchKey", "404", "NotFound"}


class S3MediaStorage(MediaStorage):
    """Stores media as private objects in an S3 bucket.

    The boto3 client is injected, so credentials, region and endpoint (e.g. LocalStack or
    MinIO) are configured by whoever builds it, and tests can pass a mocked client.
    """

    def __init__(
        self,
        client,
        bucket: str,
        *,
        key_prefix: str = "",
        server_side_encryption: str | None = "AES256",
        kms_key_id: str | None = None,
    ):
        if not bucket:
            raise ValueError("An S3 bucket name is required.")
        self._client = client
        self._bucket = bucket
        self._prefix = key_prefix.strip("/") + "/" if key_prefix.strip("/") else ""
        self._sse = server_side_encryption or None
        self._kms_key_id = kms_key_id or None

    def _object_key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def save(self, key: str, stream: BinaryIO, *, content_type: str, size: int) -> StoredObject:
        extra = {"ContentType": content_type}
        if self._sse:
            extra["ServerSideEncryption"] = self._sse
            if self._sse == "aws:kms" and self._kms_key_id:
                extra["SSEKMSKeyId"] = self._kms_key_id
        try:
            self._client.upload_fileobj(stream, self._bucket, self._object_key(key), ExtraArgs=extra)
        except (BotoCoreError, ClientError) as exc:
            log.exception("S3 upload failed for %s", key)
            raise StorageError(f"Could not upload {key} to S3.") from exc
        return StoredObject(key=key, size=size, content_type=content_type)

    def open(self, key: str) -> BinaryIO:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._object_key(key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _NOT_FOUND_CODES:
                raise StoredObjectNotFound(key) from exc
            log.exception("S3 download failed for %s", key)
            raise StorageError(f"Could not read {key} from S3.") from exc
        except BotoCoreError as exc:
            log.exception("S3 download failed for %s", key)
            raise StorageError(f"Could not read {key} from S3.") from exc
        return response["Body"]

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=self._object_key(key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _NOT_FOUND_CODES:
                return False
            raise StorageError(f"Could not check {key} in S3.") from exc
        except BotoCoreError as exc:
            raise StorageError(f"Could not check {key} in S3.") from exc
        return True

    def presign_upload(self, key: str, *, max_size: int, expires_in: int) -> DirectUpload:
        """A presigned POST policy: S3 itself refuses anything empty, larger than max_size, for
        another key, or sent after expires_in seconds. Signed with this process's credentials
        (on EC2 the instance role's, which adds x-amz-security-token), so keep it short-lived."""
        fields: dict[str, str] = {}
        conditions: list = [["content-length-range", 1, max_size]]
        if self._sse:
            # Pre-filled fields must also appear as conditions, or S3 rejects the policy.
            fields["x-amz-server-side-encryption"] = self._sse
            conditions.append({"x-amz-server-side-encryption": self._sse})
            if self._sse == "aws:kms" and self._kms_key_id:
                fields["x-amz-server-side-encryption-aws-kms-key-id"] = self._kms_key_id
                conditions.append({"x-amz-server-side-encryption-aws-kms-key-id": self._kms_key_id})
        try:
            post = self._client.generate_presigned_post(
                self._bucket, self._object_key(key), Fields=fields, Conditions=conditions, ExpiresIn=expires_in
            )
        except (BotoCoreError, ClientError) as exc:
            log.exception("Could not presign an upload for %s", key)
            raise StorageError(f"Could not prepare an upload for {key}.") from exc
        return DirectUpload(url=post["url"], fields=post["fields"])

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=self._object_key(key))
        except (BotoCoreError, ClientError) as exc:
            log.exception("S3 delete failed for %s", key)
            raise StorageError(f"Could not delete {key} from S3.") from exc

    def delete_many(self, keys: list[str]) -> None:
        # DeleteObjects takes up to 1000 keys per request.
        for start in range(0, len(keys), 1000):
            batch = keys[start : start + 1000]
            try:
                response = self._client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": [{"Key": self._object_key(k)} for k in batch], "Quiet": True},
                )
            except (BotoCoreError, ClientError) as exc:
                log.exception("S3 batch delete failed")
                raise StorageError("Could not delete media from S3.") from exc
            if response.get("Errors"):
                log.error("S3 batch delete left objects behind: %s", response["Errors"])
                raise StorageError("Some media could not be deleted from S3.")
