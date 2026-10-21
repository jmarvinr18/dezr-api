from app.services.storage.base import MediaStorage
from app.services.storage.local import LocalMediaStorage
from app.services.storage.s3 import S3MediaStorage


def create_s3_client(config: dict):
    """A boto3 S3 client. Credentials come from the standard AWS chain (env vars,
    shared config/profile, or the ECS/EC2 role); none are read from app config."""
    import boto3
    from botocore.config import Config as BotoConfig

    return boto3.client(
        "s3",
        region_name=config.get("S3_REGION") or None,
        endpoint_url=config.get("S3_ENDPOINT_URL") or None,
        # Fail fast when S3 is unreachable so uploads return 503 instead of hanging a worker.
        config=BotoConfig(
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=30,
            signature_version="s3v4",
        ),
    )


def build_media_storage(config: dict) -> MediaStorage:
    """Picks the storage backend from STORAGE_BACKEND ('s3' or 'local')."""
    backend = (config.get("STORAGE_BACKEND") or "s3").lower()

    if backend == "s3":
        bucket = config.get("S3_BUCKET")
        if not bucket:
            raise RuntimeError("STORAGE_BACKEND is 's3' but S3_BUCKET is not set.")
        return S3MediaStorage(
            create_s3_client(config),
            bucket,
            key_prefix=config.get("S3_KEY_PREFIX", ""),
            server_side_encryption=config.get("S3_SERVER_SIDE_ENCRYPTION"),
            kms_key_id=config.get("S3_KMS_KEY_ID"),
        )
    if backend == "local":
        return LocalMediaStorage(config["UPLOAD_FOLDER"])

    raise RuntimeError(f"Unknown STORAGE_BACKEND '{backend}'. Use 's3' or 'local'.")
