from unittest.mock import patch

from app.services.storage import S3MediaStorage, StorageError
from tests.conftest import BUCKET, JPG, PDF, PNG, bucket_keys, upload


def test_upload_stores_the_file_in_s3_under_a_random_key(client, admin_headers, aws):
    response = upload(client, admin_headers, "Partner Photo (1).JPG", JPG)

    assert response.status_code == 201
    file = response.get_json()
    assert (file["name"], file["type"], file["size"], file["url"]) == (
        "partner-photo-1.jpg", "image/jpeg", len(JPG), "/media/partner-photo-1.jpg"
    )
    assert file["uploadedBy"] == "admin@example.test"
    [key] = bucket_keys(aws)
    assert key.startswith("dezr/media/") and "partner" not in key


def test_type_comes_from_the_content_not_the_name(client, admin_headers):
    # A PNG named .pdf is stored as the PNG it is.
    response = upload(client, admin_headers, "brochure.pdf", PNG)
    assert response.get_json()["name"] == "brochure.png"
    assert response.get_json()["type"] == "image/png"


def test_names_are_made_unique(client, admin_headers):
    names = [upload(client, admin_headers, "Doing Business.pdf", PDF).get_json()["name"] for _ in range(3)]
    assert names == ["doing-business.pdf", "doing-business-2.pdf", "doing-business-3.pdf"]


def test_rejects_scripts_and_unknown_types(client, admin_headers, aws):
    for name, content in [("x.svg", b"<svg onload=alert(1)>"), ("x.html", b"<script>"), ("x.pdf", b"")]:
        response = upload(client, admin_headers, name, content)
        assert response.status_code == 422, name
    assert bucket_keys(aws) == []


def test_missing_file_part_is_422(client, admin_headers):
    response = client.post("/api/v1/admin/media", data={}, headers=admin_headers, content_type="multipart/form-data")
    assert response.status_code == 422


def test_too_large_is_413(app, client, admin_headers):
    big = b"%PDF-" + b"0" * app.config["MAX_CONTENT_LENGTH"]
    assert upload(client, admin_headers, "big.pdf", big).status_code == 413


def test_public_download_streams_with_safe_headers(client, admin_headers):
    upload(client, admin_headers, "guide.pdf", PDF)

    response = client.get("/api/v1/media/guide.pdf")

    assert response.status_code == 200
    assert response.data == PDF
    assert response.mimetype == "application/pdf"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response.headers["Content-Security-Policy"]
    assert response.headers["Content-Disposition"].startswith("inline")


def test_list_and_delete(client, admin_headers, aws):
    upload(client, admin_headers, "a.png", PNG)
    upload(client, admin_headers, "b.pdf", PDF)

    listed = client.get("/api/v1/admin/media", headers=admin_headers).get_json()
    assert [f["name"] for f in listed] == ["b.pdf", "a.png"]  # newest first

    assert client.delete("/api/v1/admin/media/a.png", headers=admin_headers).status_code == 204
    assert client.get("/api/v1/media/a.png").status_code == 404
    assert len(bucket_keys(aws)) == 1


def test_unknown_names_are_404(client, admin_headers):
    for name in ["nope.pdf", "..%2Fsecret", "UPPER.PDF"]:
        assert client.get(f"/api/v1/media/{name}").status_code == 404
    assert client.delete("/api/v1/admin/media/nope.pdf", headers=admin_headers).status_code == 404


def test_s3_outage_returns_503_and_records_nothing(client, admin_headers):
    with patch.object(S3MediaStorage, "save", side_effect=StorageError("down")):
        response = upload(client, admin_headers, "a.pdf", PDF)

    assert response.status_code == 503
    assert client.get("/api/v1/admin/media", headers=admin_headers).get_json() == []


def test_missing_object_is_404(client, admin_headers, aws):
    upload(client, admin_headers, "a.pdf", PDF)
    for key in bucket_keys(aws):
        aws.delete_object(Bucket=BUCKET, Key=key)
    assert client.get("/api/v1/media/a.pdf").status_code == 404


def test_upload_needs_an_admin(client):
    assert upload(client, {}, "a.pdf", PDF).status_code == 401
