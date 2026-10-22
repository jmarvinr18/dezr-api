import io
import os
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

# Never let tests reach real AWS, even if app/.env holds real credentials.
os.environ.update(
    AWS_ACCESS_KEY_ID="testing",
    AWS_SECRET_ACCESS_KEY="testing",
    AWS_SESSION_TOKEN="testing",
    AWS_DEFAULT_REGION="ap-southeast-1",
)
os.environ.pop("AWS_PROFILE", None)

from flask_migrate import downgrade, upgrade  # noqa: E402

from app import create_app  # noqa: E402
from tests.document_fakes import FakeEmbedder  # noqa: E402

PROJECT_DIR = Path(__file__).resolve().parents[1]
# Run the suite against PostgreSQL (pgvector) instead of SQLite, e.g.
# TEST_DATABASE_URL=postgresql://dezr:...@localhost:5435/dezr_test uv run pytest
# The database is emptied (migrated down and up) for every test.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BUCKET = "dezr-test"
REGION = "ap-southeast-1"

ADMIN_EMAIL = "admin@example.test"
ADMIN_PASSWORD = "correct horse battery"

PDF = b"%PDF-1.4\n% test document\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 8


@pytest.fixture
def aws():
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        yield s3


def make_app(tmp_path, embedder=None, knowledge_base=None, **overrides):
    """A migrated app on a fresh database, with files in the mocked S3 bucket and a fake
    embedding model. Documents are processed only when a test asks (INGESTION_MODE=worker)."""
    app = create_app(
        TEST_DATABASE_URL or "sqlite:///" + (tmp_path / "test.db").as_posix(),
        embedder=embedder or FakeEmbedder(),
        knowledge_base=knowledge_base,
        config_overrides={
            "TESTING": True,
            "SECRET_KEY": "test-secret-key-that-is-long-enough",
            "STORAGE_BACKEND": "s3",
            "S3_BUCKET": BUCKET,
            "S3_REGION": REGION,
            "S3_ENDPOINT_URL": None,
            "S3_KEY_PREFIX": "dezr",
            "TRUSTED_PROXY_COUNT": 0,
            "CORS_ORIGINS": ["http://localhost:3000"],
            "INGESTION_MODE": "worker",
            # Never reach a real agent, whatever app/.env says; tests install fakes.
            "AI_AGENT_RUNTIME_ARN": None,
            "AI_AGENT_URL": None,
            "AI_API_KEY": None,
            **overrides,
        },
    )
    with app.app_context():
        if TEST_DATABASE_URL:
            downgrade(directory=str(PROJECT_DIR / "migrations"), revision="base")
        upgrade(directory=str(PROJECT_DIR / "migrations"))
    return app


@pytest.fixture
def app(tmp_path, aws):
    return make_app(tmp_path)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seeded(app):
    """The shipped pages, as after `flask seed-content`."""
    result = app.test_cli_runner().invoke(args=["seed-content"])
    assert result.exit_code == 0, result.output
    return result.output


@pytest.fixture
def admin_user(app):
    result = app.test_cli_runner().invoke(args=["admin", "create", ADMIN_EMAIL, "--password", ADMIN_PASSWORD])
    assert result.exit_code == 0, result.output
    return ADMIN_EMAIL


def login(client, email=ADMIN_EMAIL, password=ADMIN_PASSWORD, **kwargs):
    return client.post("/api/v1/admin/auth/login", json={"email": email, "password": password}, **kwargs)


@pytest.fixture
def admin_headers(client, admin_user):
    response = login(client)
    assert response.status_code == 200, response.get_json()
    return {"Authorization": f"Bearer {response.get_json()['token']}"}


def upload(client, headers, name, content):
    return client.post(
        "/api/v1/admin/media",
        data={"file": (io.BytesIO(content), name)},
        headers=headers,
        content_type="multipart/form-data",
    )


def bucket_keys(s3):
    return sorted(o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET).get("Contents", []))
