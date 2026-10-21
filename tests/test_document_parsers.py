import io
import zipfile

import pytest

from app.services.document_validator import DocumentValidator
from app.services.errors import PayloadTooLarge, UnsupportedMediaType, ValidationFailed
from app.services.ingestion import UnreadableDocument, parse_document
from app.services.ingestion.parsers.pdf import NO_TEXT, _is_password_error
from app.services.upload_validator import IncomingFile
from tests.document_fakes import make_docx, make_pdf, table_rules


def kinds(blocks):
    return [(b.kind, b.level, b.text) for b in blocks]


# ---------- Markdown ----------


def test_markdown_structure():
    text = """---
title: ignored front matter
---
# Guide

Intro **text**.

## Fees

- Hourly
- Fixed
  continued

| Service | Fee |
| --- | --- |
| Visa | PHP 30,000 |

```
code block
```

> A quote.
"""
    blocks = parse_document("md", text.encode())
    assert [(b.kind, b.level) for b in blocks] == [
        ("heading", 1), ("paragraph", 0), ("heading", 2), ("list", 0), ("table", 0), ("code", 0), ("quote", 0),
    ]
    assert blocks[3].parts == ["- Hourly", "- Fixed\n  continued"]
    assert blocks[4].header == "| Service | Fee |\n| --- | --- |" and blocks[4].parts == ["| Visa | PHP 30,000 |"]
    assert "ignored front matter" not in " ".join(b.text for b in blocks)


# ---------- Plain text ----------


def test_plain_text_infers_headings_lists_and_tables():
    text = (
        "ENGAGEMENT TERMS\n\n"
        "These terms apply to every matter we handle\nfor the client.\n\n"
        "1.2 Billing\n\n"
        "- Invoices are monthly\n- Payment within 30 days\n\n"
        "Service\tFee\nVisa\tPHP 30,000\n\n"
        "ARTICLE IV\n\n"
        "Disputes go to arbitration in Makati.\n"
    )
    blocks = parse_document("txt", text.encode("utf-8"))
    assert kinds(blocks) == [
        ("heading", 1, "ENGAGEMENT TERMS"),
        ("paragraph", 0, "These terms apply to every matter we handle for the client."),
        ("heading", 2, "1.2 Billing"),
        ("list", 0, "- Invoices are monthly\n- Payment within 30 days"),
        ("table", 0, "| Service | Fee |\n| --- | --- |\n| Visa | PHP 30,000 |"),
        ("heading", 1, "ARTICLE IV"),
        ("paragraph", 0, "Disputes go to arbitration in Makati."),
    ]


def test_plain_text_decodes_windows_1252_and_utf16():
    assert parse_document("txt", "Café terms.".encode("cp1252"))[0].text == "Café terms."
    assert parse_document("txt", "日本語の資料です。".encode("utf-16"))[0].text == "日本語の資料です。"


# ---------- Word ----------


def test_docx_structure_from_styles():
    blocks = parse_document("docx", make_docx())
    assert [(b.kind, b.level) for b in blocks] == [
        ("heading", 0), ("heading", 1), ("paragraph", 0), ("list", 0), ("heading", 2),
        ("paragraph", 0), ("table", 0), ("heading", 1), ("paragraph", 0),
    ]
    assert blocks[3].parts == ["- Hourly work", "- Fixed fees for incorporations"]
    assert blocks[6].text.startswith("| Service | Fee |\n| --- | --- |\n| Incorporation | PHP 50,000 |")


def test_damaged_docx_is_unreadable():
    with pytest.raises(UnreadableDocument):
        parse_document("docx", b"PK\x03\x04 not really a zip")


# ---------- PDF ----------


def _guide_pdf():
    footer = [("Donato & Zarate - Confidential", 72, 770, 9, False)]
    page1 = {
        "lines": [
            ("Doing Business Guide", 72, 72, 24, True),
            ("1. Corporate Law", 72, 110, 16, True),
            ("Foreign companies may set up a branch or a subsidiary. The SEC re-", 72, 135, 11, False),
            ("views every application within thirty days.", 72, 149, 11, False),
            ("Required documents", 72, 175, 11, True),
            ("• Articles of incorporation", 80, 195, 11, False),
            ("• Proof of capital", 80, 209, 11, False),
            ("Service", 77, 245, 11, False), ("Fee", 257, 245, 11, False),
            ("Incorporation", 77, 265, 11, False), ("PHP 50,000", 257, 265, 11, False),
            ("Page 1 of 2", 500, 770, 9, False),
            *footer,
        ],
        "rules": table_rules(72, 232, [180, 150], 20, 2),
    }
    page2 = {
        "lines": [
            ("2. Taxation", 72, 72, 16, True),
            ("Corporations pay income tax on their net taxable income.", 72, 97, 11, False),
            ("Page 2 of 2", 500, 770, 9, False),
            *footer,
        ]
    }
    return make_pdf([page1, page2])


def test_pdf_structure_from_layout():
    blocks = parse_document("pdf", _guide_pdf())
    assert [(b.kind, b.level, b.page) for b in blocks] == [
        ("heading", 1, 1),
        ("heading", 2, 1),
        ("paragraph", 0, 1),
        ("heading", 3, 1),
        ("list", 0, 1),
        ("table", 0, 1),
        ("heading", 2, 2),
        ("paragraph", 0, 2),
    ]
    # Hyphenation across lines is undone.
    assert "The SEC reviews every application" in blocks[2].text
    assert blocks[5].text == "| Service | Fee |\n| --- | --- |\n| Incorporation | PHP 50,000 |"
    # Running footers and page numbers are dropped.
    text = " ".join(b.text for b in blocks)
    assert "Confidential" not in text and "Page 1" not in text
    # Table words don't leak into paragraphs.
    assert "PHP 50,000" not in " ".join(b.text for b in blocks if b.kind != "table")


def test_pdf_without_text_is_refused_as_a_scan():
    with pytest.raises(UnreadableDocument) as exc:
        parse_document("pdf", make_pdf([{"lines": [], "rules": [(72, 72, 300, 300)]}]))
    assert exc.value.message == NO_TEXT


def test_damaged_pdf_is_unreadable():
    with pytest.raises(UnreadableDocument) as exc:
        parse_document("pdf", b"%PDF-1.4\nnot a real pdf")
    assert "could not be read" in exc.value.message


def test_password_errors_are_recognised_through_wrapping():
    class PDFPasswordIncorrect(Exception):
        pass

    wrapped = RuntimeError("open failed")
    wrapped.__cause__ = PDFPasswordIncorrect()
    assert _is_password_error(wrapped)
    assert not _is_password_error(ValueError("bad xref"))


# ---------- Validation ----------


def incoming(name, content):
    return IncomingFile(name, io.BytesIO(content))


@pytest.mark.parametrize(
    ("name", "content", "kind"),
    [
        ("guide.pdf", make_pdf([{"lines": [("Hi", 72, 72, 11, False)]}]), "pdf"),
        ("renamed.txt", make_pdf([{"lines": [("Hi", 72, 72, 11, False)]}]), "pdf"),  # content wins
        ("terms.docx", make_docx(), "docx"),
        ("notes.TXT", "Plain notes.".encode(), "txt"),
        ("readme.md", b"# Title\n\nText.", "md"),
        ("readme.markdown", b"# Title", "md"),
    ],
    ids=["pdf", "pdf-renamed", "docx", "txt", "md", "markdown"],
)
def test_validator_recognises_types_by_content(name, content, kind):
    document = DocumentValidator().validate(incoming(name, content))
    assert document.kind == kind
    assert document.stream.tell() == 0


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("virus.exe", b"MZ\x90\x00\x03", "Upload a PDF"),
        ("notes.txt", b"\x00\x01\x02binary\x00data", "Upload a PDF"),
        ("old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64, ".doc"),
        ("page.html", b"<html><body>Hi</body></html>", "Upload a PDF"),
    ],
    ids=["exe", "binary-txt", "legacy-doc", "html"],
)
def test_validator_refuses_other_types(name, content, message):
    with pytest.raises(UnsupportedMediaType) as exc:
        DocumentValidator().validate(incoming(name, content))
    assert message in exc.value.message


def test_validator_refuses_zips_that_are_not_word_files():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("xl/workbook.xml", "<workbook/>")
        z.writestr("[Content_Types].xml", "<Types/>")
    with pytest.raises(UnsupportedMediaType):
        DocumentValidator().validate(incoming("sheet.docx", archive.getvalue()))


def test_validator_refuses_empty_and_oversized_files():
    with pytest.raises(ValidationFailed):
        DocumentValidator().validate(incoming("empty.txt", b""))
    with pytest.raises(ValidationFailed):
        DocumentValidator().validate(None)
    with pytest.raises(PayloadTooLarge):
        DocumentValidator(max_file_size=10).validate(incoming("big.txt", b"x" * 11))
