import io

import pytest

from app.services.errors import ValidationFailed
from app.services.upload_validator import IncomingFile, UploadValidator, slugify_name
from tests.conftest import JPG, PDF, PNG, WEBP


def incoming(name, content):
    return IncomingFile(name, io.BytesIO(content))


@pytest.mark.parametrize(
    ("content", "extension", "content_type"),
    [
        (PDF, "pdf", "application/pdf"),
        (PNG, "png", "image/png"),
        (JPG, "jpg", "image/jpeg"),
        (b"GIF89a" + b"\x00" * 8, "gif", "image/gif"),
        (WEBP, "webp", "image/webp"),
    ],
)
def test_recognises_accepted_types_by_content(content, extension, content_type):
    upload = UploadValidator().validate(incoming("whatever.bin", content))
    assert (upload.extension, upload.content_type, upload.size) == (extension, content_type, len(content))
    assert upload.stream.tell() == 0  # rewound for the storage backend


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        (b"", "empty"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "Upload a PNG"),
        (b"MZ\x90\x00", "Upload a PNG"),
        (b"%PDF-" + b"0" * 2048, "up to"),
    ],
)
def test_rejects_bad_files(content, problem):
    with pytest.raises(ValidationFailed) as exc:
        UploadValidator(max_file_size=1024 * 1024 if problem != "up to" else 1024).validate(incoming("f.pdf", content))
    assert problem in exc.value.message


def test_requires_a_file():
    with pytest.raises(ValidationFailed):
        UploadValidator().validate(None)


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Partner Photo (1).JPG", "partner-photo-1"),
        ("C:\\scans\\Résumé.pdf", "resume"),
        ("../../etc/passwd", "passwd"),
        ("フィリピン.pdf", "file"),
        ("a" * 59 + " b.pdf", "a" * 59),
    ],
)
def test_slugify_name(name, slug):
    assert slugify_name(name) == slug
