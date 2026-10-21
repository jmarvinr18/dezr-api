import base64
import io
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import requests
from sqlalchemy import select, update

from app.constants import DOCUMENT_KINDS
from app.database import db
from app.extensions.ingestion import get_ingestion_service
from app.model import Document, DocumentChunk
from app.services.ingestion_service import EMBEDDING_FAILED, GAVE_UP
from app.services.storage import S3MediaStorage, StorageError
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, BUCKET, REGION, bucket_keys, login, make_app
from tests.document_fakes import FakeEmbedder, make_docx, make_pdf

URL = "/api/v1/admin/documents"

GUIDE_MD = b"""# Doing Business in the Philippines

## Corporate

Foreign companies may register a branch or a subsidiary with the SEC.

### Requirements

- Articles of incorporation
- Proof of paid-up capital

## Taxation

| Tax | Rate |
| --- | --- |
| Corporate income tax | 25% |
| VAT | 12% |
"""


# Sections long enough to stay passages of their own (not merged with their neighbours).
LONG_MD = (
    "# Doing Business in the Philippines\n\n## Corporate\n\n"
    + "Foreign companies may register a branch or a subsidiary with the SEC in Makati. " * 8
    + "\n\n## Taxation\n\n"
    + "Corporations pay income tax on net taxable income and file returns every quarter. " * 8
).encode()


def upload(client, headers, name, content, title=None):
    data = {"file": (io.BytesIO(content), name)}
    if title is not None:
        data["title"] = title
    return client.post(URL, data=data, headers=headers, content_type="multipart/form-data")


def process(app):
    with app.app_context():
        return get_ingestion_service().run_pending()


def chunks_of(client, headers, document_id):
    return client.get(f"{URL}/{document_id}/chunks", headers=headers).get_json()


# ---------- upload and processing ----------


def test_upload_returns_processing_then_becomes_ready(app, client, admin_headers, aws):
    response = upload(client, admin_headers, "doing-business-ph.md", GUIDE_MD, title="  Doing business  ")

    assert response.status_code == 202
    document = response.get_json()
    assert document["status"] == "processing"
    assert (document["title"], document["fileName"], document["contentType"]) == (
        "Doing business", "doing-business-ph.md", "text/markdown"
    )
    assert document["chunkCount"] is None and document["error"] is None and document["processedAt"] is None
    assert document["uploadedBy"] == ADMIN_EMAIL
    [key] = bucket_keys(aws)
    assert key.startswith("dezr/documents/") and "doing-business" not in key

    assert process(app) == 1

    ready = client.get(f"{URL}/{document['id']}", headers=admin_headers).get_json()
    assert ready["status"] == "ready" and ready["chunkCount"] >= 1 and ready["processedAt"]
    passages = chunks_of(client, admin_headers, document["id"])
    assert passages["total"] == ready["chunkCount"]
    assert all(p["hasEmbedding"] for p in passages["items"])
    table = next(p for p in passages["items"] if "table" in p["blockTypes"])
    assert "| VAT | 12% |" in table["content"]
    # The short sections were merged; their headings are kept in the text.
    assert table["headingPath"] == ["Doing Business in the Philippines"]
    assert "## Taxation" in table["content"] and "### Requirements" in table["content"]


def test_passages_are_embedded_with_their_context(tmp_path, aws):
    embedder = FakeEmbedder()
    app = make_app(tmp_path, embedder=embedder)
    client = app.test_client()
    app.test_cli_runner().invoke(args=["admin", "create", ADMIN_EMAIL, "--password", ADMIN_PASSWORD])
    headers = {"Authorization": f"Bearer {login(client).get_json()['token']}"}

    upload(client, headers, "guide.md", LONG_MD, title="Client guide")
    process(app)

    [texts] = embedder.calls
    assert len(texts) == 2
    assert texts[0].startswith("Client guide\nDoing Business in the Philippines > Corporate\n\nForeign companies")
    assert texts[1].startswith("Client guide\nDoing Business in the Philippines > Taxation\n\nCorporations pay")
    with app.app_context():
        assert db.session.scalar(select(Document.embedding_model)) == "fake-embed-v1"


def test_title_defaults_to_the_file_name(client, admin_headers):
    document = upload(client, admin_headers, "Engagement Terms 2026.docx", make_docx()).get_json()
    assert document["title"] == "Engagement Terms 2026"
    assert document["fileName"] == "Engagement Terms 2026.docx"


def test_word_and_pdf_documents_are_chunked(app, client, admin_headers):
    pdf = make_pdf(
        [
            {"lines": [("Visa Guide", 72, 72, 20, True), ("Investors may apply for an SIRV visa.", 72, 100, 11, False)]},
            {"lines": [("Retirees", 72, 72, 14, True), ("Retirees may apply for an SRRV visa.", 72, 100, 11, False)]},
        ]
    )
    docx_id = upload(client, admin_headers, "terms.docx", make_docx()).get_json()["id"]
    pdf_id = upload(client, admin_headers, "visas.pdf", pdf).get_json()["id"]
    assert process(app) == 2

    word = chunks_of(client, admin_headers, docx_id)["items"]
    assert word[0]["headingPath"][0] == "Engagement Guide"
    assert any("| Incorporation | PHP 50,000 |" in p["content"] for p in word)
    assert all(p["pageStart"] is None for p in word)

    pdf_passages = chunks_of(client, admin_headers, pdf_id)["items"]
    assert (pdf_passages[0]["pageStart"], pdf_passages[-1]["pageEnd"]) == (1, 2)
    assert "SRRV" in " ".join(p["content"] for p in pdf_passages)


def test_list_is_newest_first(client, admin_headers):
    first = upload(client, admin_headers, "a.txt", b"First document.").get_json()
    second = upload(client, admin_headers, "b.txt", b"Second document.").get_json()
    listed = client.get(URL, headers=admin_headers).get_json()
    assert [d["id"] for d in listed] == [second["id"], first["id"]]


# ---------- failures ----------


def test_scanned_pdf_fails_with_an_actionable_message(app, client, admin_headers):
    document = upload(client, admin_headers, "scan.pdf", make_pdf([{"lines": []}])).get_json()
    process(app)
    failed = client.get(f"{URL}/{document['id']}", headers=admin_headers).get_json()
    assert failed["status"] == "failed"
    assert failed["error"].startswith("The PDF has no readable text.")
    assert failed["chunkCount"] is None


def test_embedding_outage_fails_the_document(tmp_path, aws):
    app = make_app(tmp_path, embedder=FakeEmbedder(fail=True))
    client = app.test_client()
    app.test_cli_runner().invoke(args=["admin", "create", ADMIN_EMAIL, "--password", ADMIN_PASSWORD])
    headers = {"Authorization": f"Bearer {login(client).get_json()['token']}"}

    document = upload(client, headers, "guide.md", GUIDE_MD).get_json()
    process(app)

    failed = client.get(f"{URL}/{document['id']}", headers=headers).get_json()
    assert (failed["status"], failed["error"]) == ("failed", EMBEDDING_FAILED)
    with app.app_context():
        assert db.session.scalar(select(DocumentChunk.id)) is None


def test_unsupported_and_oversized_uploads(client, admin_headers, aws):
    assert upload(client, admin_headers, "virus.exe", b"MZ\x90\x00").status_code == 415
    legacy = upload(client, admin_headers, "old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    assert legacy.status_code == 415 and ".docx" in legacy.get_json()["message"]

    # Over the media limit (11 MB) but within the documents limit: accepted.
    medium = b"Plain text line.\n" * (12 * 1024 * 1024 // 17)
    assert upload(client, admin_headers, "medium.txt", medium).status_code == 202
    # Over 25 MB.
    huge = b"x" * (26 * 1024 * 1024)
    assert upload(client, admin_headers, "huge.txt", huge).status_code == 413
    assert len(bucket_keys(aws)) == 1


def test_title_too_long_is_422_on_the_title_field(client, admin_headers, aws):
    response = upload(client, admin_headers, "a.txt", b"Text.", title="x" * 201)
    assert response.status_code == 422
    assert response.get_json()["errors"] == {"title": "Use a title of up to 200 characters."}
    assert bucket_keys(aws) == []


def test_missing_file_is_422(client, admin_headers):
    response = client.post(URL, data={"title": "No file"}, headers=admin_headers, content_type="multipart/form-data")
    assert response.status_code == 422


def test_storage_outage_is_503_and_records_nothing(client, admin_headers):
    with patch.object(S3MediaStorage, "save", side_effect=StorageError("down")):
        assert upload(client, admin_headers, "a.txt", b"Text.").status_code == 503
    assert client.get(URL, headers=admin_headers).get_json() == []


# ---------- direct uploads (browser -> S3, then confirm) ----------


def start_upload(client, headers, size):
    return client.post(f"{URL}/uploads", json={"size": size}, headers=headers)


def send_to_s3(form, name, content):
    """What the browser does with the form: a multipart POST of every field, then the file."""
    return requests.post(form["url"], data=form["fields"], files={"file": (name, content)}, timeout=10)


def complete(client, headers, upload_id, file_name, title=None):
    body = {"fileName": file_name} if file_name is not None else {}
    if title is not None:
        body["title"] = title
    return client.post(f"{URL}/uploads/{upload_id}/complete", json=body, headers=headers)


def test_direct_upload_is_checked_stored_and_queued(app, client, admin_headers, aws):
    start = start_upload(client, admin_headers, len(GUIDE_MD))
    assert start.status_code == 201
    form = start.get_json()
    staged = f"dezr/incoming/{form['uploadId']}"
    assert form["fields"]["key"] == staged and form["maxSize"] == 25 * 1024 * 1024
    assert form["url"] == f"https://{BUCKET}.s3.{REGION}.amazonaws.com/"
    expires = datetime.fromisoformat(form["expiresAt"].replace("Z", "+00:00"))
    assert timedelta(minutes=14) < expires - datetime.now(timezone.utc) <= timedelta(minutes=15)
    # S3 enforces the limits itself: the key, 1 byte to 25 MB, and the bucket's encryption.
    policy = json.loads(base64.b64decode(form["fields"]["policy"]))
    assert ["content-length-range", 1, 25 * 1024 * 1024] in policy["conditions"]
    assert {"key": staged} in policy["conditions"] and {"bucket": BUCKET} in policy["conditions"]
    assert form["fields"]["x-amz-server-side-encryption"] == "AES256"
    # Nothing is recorded (or claimable by a processor) before the file is confirmed.
    assert client.get(URL, headers=admin_headers).get_json() == []

    assert send_to_s3(form, "guide.md", GUIDE_MD).status_code == 204
    response = complete(client, admin_headers, form["uploadId"], "doing-business-ph.md", title="  Doing business ")

    assert response.status_code == 202
    document = response.get_json()
    assert (document["status"], document["title"], document["fileName"], document["contentType"], document["size"]) == (
        "processing", "Doing business", "doing-business-ph.md", "text/markdown", len(GUIDE_MD)
    )
    assert document["uploadedBy"] == ADMIN_EMAIL
    # Moved out of incoming/ to a document key, as if uploaded through the API.
    [key] = bucket_keys(aws)
    assert key.startswith("dezr/documents/") and key.endswith(".md")
    assert process(app) == 1
    assert client.get(f"{URL}/{document['id']}", headers=admin_headers).get_json()["status"] == "ready"
    # Confirming twice finds nothing to confirm.
    assert complete(client, admin_headers, form["uploadId"], "doing-business-ph.md").status_code == 404


def test_direct_upload_kind_comes_from_the_content(client, admin_headers, aws):
    form = start_upload(client, admin_headers, 100).get_json()
    aws.put_object(Bucket=BUCKET, Key=form["fields"]["key"], Body=make_docx())
    document = complete(client, admin_headers, form["uploadId"], "terms.pdf").get_json()
    assert document["contentType"] == DOCUMENT_KINDS["docx"] and document["fileName"] == "terms.pdf"
    assert bucket_keys(aws)[0].endswith(".docx")


def test_a_direct_upload_that_fails_the_checks_is_deleted(client, admin_headers, aws):
    form = start_upload(client, admin_headers, 4).get_json()
    aws.put_object(Bucket=BUCKET, Key=form["fields"]["key"], Body=b"MZ\x90\x00")

    response = complete(client, admin_headers, form["uploadId"], "virus.pdf")

    assert response.status_code == 415
    assert bucket_keys(aws) == []
    assert client.get(URL, headers=admin_headers).get_json() == []


def test_an_oversized_direct_upload_is_refused_and_deleted(client, admin_headers, aws):
    # S3 refuses these itself (content-length-range); the API checks again in case it didn't.
    form = start_upload(client, admin_headers, 100).get_json()
    aws.put_object(Bucket=BUCKET, Key=form["fields"]["key"], Body=b"x" * (25 * 1024 * 1024 + 1))
    assert complete(client, admin_headers, form["uploadId"], "huge.txt").status_code == 413
    assert bucket_keys(aws) == []


def test_a_bad_title_or_missing_name_keeps_the_file_for_a_retry(client, admin_headers, aws):
    form = start_upload(client, admin_headers, 5).get_json()
    aws.put_object(Bucket=BUCKET, Key=form["fields"]["key"], Body=b"Text.")

    too_long = complete(client, admin_headers, form["uploadId"], "a.txt", title="x" * 201)
    assert too_long.status_code == 422
    assert too_long.get_json()["errors"] == {"title": "Use a title of up to 200 characters."}
    no_name = complete(client, admin_headers, form["uploadId"], None)
    assert no_name.status_code == 422 and "fileName" in no_name.get_json()["errors"]
    assert bucket_keys(aws) == [form["fields"]["key"]]

    assert complete(client, admin_headers, form["uploadId"], "a.txt", title="Short").status_code == 202


def test_confirming_an_upload_that_never_arrived_is_404(client, admin_headers):
    form = start_upload(client, admin_headers, 5).get_json()
    response = complete(client, admin_headers, form["uploadId"], "a.txt")
    assert response.status_code == 404 and "upload it again" in response.get_json()["message"]
    assert complete(client, admin_headers, uuid.uuid4(), "a.txt").status_code == 404


def test_starting_an_upload_checks_the_size(client, admin_headers):
    assert start_upload(client, admin_headers, 0).status_code == 422
    too_big = start_upload(client, admin_headers, 25 * 1024 * 1024 + 1)
    assert too_big.status_code == 413 and too_big.get_json()["message"] == "Documents can be up to 25 MB."
    assert client.post(f"{URL}/uploads", json={}, headers=admin_headers).status_code == 422


def test_direct_uploads_need_s3_storage(tmp_path):
    app = make_app(tmp_path, STORAGE_BACKEND="local", UPLOAD_FOLDER=str(tmp_path / "uploads"))
    client = app.test_client()
    app.test_cli_runner().invoke(args=["admin", "create", ADMIN_EMAIL, "--password", ADMIN_PASSWORD])
    headers = {"Authorization": f"Bearer {login(client).get_json()['token']}"}

    response = start_upload(client, headers, 100)

    assert response.status_code == 501 and "POST /api/v1/admin/documents" in response.get_json()["message"]
    # The multipart upload still works there.
    assert upload(client, headers, "a.txt", b"Text.").status_code == 202


def test_storage_outage_while_starting_or_confirming_is_503(client, admin_headers, aws):
    with patch.object(S3MediaStorage, "presign_upload", side_effect=StorageError("down")):
        assert start_upload(client, admin_headers, 5).status_code == 503

    form = start_upload(client, admin_headers, 5).get_json()
    aws.put_object(Bucket=BUCKET, Key=form["fields"]["key"], Body=b"Text.")
    with patch.object(S3MediaStorage, "save", side_effect=StorageError("down")):
        assert complete(client, admin_headers, form["uploadId"], "a.txt").status_code == 503
    # Still waiting in incoming/, so confirming again once S3 is back works.
    assert complete(client, admin_headers, form["uploadId"], "a.txt").status_code == 202


# ---------- delete ----------


def test_delete_removes_file_passages_and_vectors(app, client, admin_headers, aws):
    document = upload(client, admin_headers, "guide.md", GUIDE_MD).get_json()
    process(app)

    assert client.delete(f"{URL}/{document['id']}", headers=admin_headers).status_code == 204

    assert client.get(f"{URL}/{document['id']}", headers=admin_headers).status_code == 404
    assert bucket_keys(aws) == []
    with app.app_context():
        assert db.session.scalar(select(DocumentChunk.id)) is None


def test_deleting_before_processing_cancels_it(app, client, admin_headers):
    document = upload(client, admin_headers, "guide.md", GUIDE_MD).get_json()
    assert client.delete(f"{URL}/{document['id']}", headers=admin_headers).status_code == 204
    assert process(app) == 0


def test_deleting_during_processing_drops_the_work(tmp_path, aws):
    holder = {}

    def delete_meanwhile():
        holder["client"].delete(f"{URL}/{holder['id']}", headers=holder["headers"])

    app = make_app(tmp_path, embedder=FakeEmbedder(on_embed=delete_meanwhile))
    client = app.test_client()
    app.test_cli_runner().invoke(args=["admin", "create", ADMIN_EMAIL, "--password", ADMIN_PASSWORD])
    headers = {"Authorization": f"Bearer {login(client).get_json()['token']}"}
    holder.update(client=client, headers=headers, id=upload(client, headers, "g.md", GUIDE_MD).get_json()["id"])
    document_id = uuid.UUID(holder["id"])

    with app.app_context():
        assert get_ingestion_service().process(document_id) == "cancelled"
        assert db.session.scalar(select(DocumentChunk.id)) is None
        assert db.session.scalar(select(Document.id)) is None
    assert bucket_keys(aws) == []


# ---------- queue ----------


def test_an_abandoned_claim_is_picked_up_again(app, client, admin_headers):
    document = upload(client, admin_headers, "guide.md", GUIDE_MD).get_json()
    with app.app_context():
        # A processor claimed it and died: attempts 1, lease still running.
        db.session.execute(
            update(Document).values(attempts=1, locked_until=datetime.now(timezone.utc) + timedelta(minutes=5))
        )
        db.session.commit()
        assert get_ingestion_service().run_pending() == 0  # still leased

        db.session.execute(update(Document).values(locked_until=datetime.now(timezone.utc) - timedelta(seconds=1)))
        db.session.commit()
        assert get_ingestion_service().run_pending() == 1
    assert client.get(f"{URL}/{document['id']}", headers=admin_headers).get_json()["status"] == "ready"


def test_a_document_that_keeps_crashing_its_processor_fails(app, client, admin_headers):
    document = upload(client, admin_headers, "guide.md", GUIDE_MD).get_json()
    with app.app_context():
        attempts = app.config["INGESTION_MAX_ATTEMPTS"]
        db.session.execute(update(Document).values(attempts=attempts, locked_until=None))
        db.session.commit()
        get_ingestion_service().run_pending()
    failed = client.get(f"{URL}/{document['id']}", headers=admin_headers).get_json()
    assert (failed["status"], failed["error"]) == ("failed", GAVE_UP)


def test_a_claimed_document_is_skipped_by_other_processors(app, client, admin_headers):
    document = upload(client, admin_headers, "guide.md", GUIDE_MD).get_json()
    with app.app_context():
        service = get_ingestion_service()
        document_id = uuid.UUID(document["id"])
        assert service._claim(document_id) is not None  # another processor holds it
        assert service.process(document_id) == "skipped"


def test_thread_mode_processes_in_the_background(tmp_path, aws):
    app = make_app(tmp_path, INGESTION_MODE="thread")
    client = app.test_client()
    app.test_cli_runner().invoke(args=["admin", "create", ADMIN_EMAIL, "--password", ADMIN_PASSWORD])
    headers = {"Authorization": f"Bearer {login(client).get_json()['token']}"}

    document = upload(client, headers, "guide.md", GUIDE_MD).get_json()
    assert document["status"] == "processing"
    app.extensions["ingestion_runner"].wait(timeout=30)

    assert client.get(f"{URL}/{document['id']}", headers=headers).get_json()["status"] == "ready"


def test_cli_reprocess_and_list(app, client, admin_headers):
    document = upload(client, admin_headers, "guide.md", GUIDE_MD).get_json()
    runner = app.test_cli_runner()
    assert "Processed 1 document(s)." in runner.invoke(args=["documents", "process-pending"]).output

    assert "Queued 1 document(s)." in runner.invoke(args=["documents", "reprocess", "--all"]).output
    assert client.get(f"{URL}/{document['id']}", headers=admin_headers).get_json()["status"] == "processing"
    runner.invoke(args=["documents", "process-pending"])
    listing = runner.invoke(args=["documents", "list"]).output
    assert document["id"] in listing and "ready" in listing


# ---------- access ----------


def test_documents_need_an_admin(client):
    assert client.get(URL).status_code == 401
    assert upload(client, {}, "a.txt", b"Text.").status_code == 401
    assert client.delete(f"{URL}/00000000-0000-0000-0000-000000000000").status_code == 401
    assert start_upload(client, {}, 5).status_code == 401
    assert complete(client, {}, uuid.uuid4(), "a.txt").status_code == 401


def test_unknown_document_is_404(client, admin_headers):
    missing = f"{URL}/00000000-0000-0000-0000-000000000000"
    assert client.get(missing, headers=admin_headers).status_code == 404
    assert client.delete(missing, headers=admin_headers).status_code == 404
    assert client.get(f"{missing}/chunks", headers=admin_headers).status_code == 404
