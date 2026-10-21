from app.services.ingestion.blocks import Block
from app.services.ingestion.errors import UnreadableDocument
from app.services.ingestion.text import decode_text

from .docx import parse_docx
from .markdown import parse_markdown
from .pdf import parse_pdf
from .plain_text import parse_plain_text


def parse_document(kind: str, data: bytes) -> list[Block]:
    """The blocks of a stored document, by its kind (pdf, docx, txt or md)."""
    if kind == "pdf":
        return parse_pdf(data)
    if kind == "docx":
        return parse_docx(data)
    if kind == "md":
        return parse_markdown(decode_text(data))
    if kind == "txt":
        return parse_plain_text(decode_text(data))
    raise UnreadableDocument(f"Documents of type '{kind}' can't be processed.")
