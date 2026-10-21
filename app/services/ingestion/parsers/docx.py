"""Word (.docx) → blocks. Word files say what their structure is: paragraph styles ("Title",
"Heading 1".."Heading 9", or an outline level), list numbering, and real tables. Body paragraphs
and tables are read in document order. Word has no fixed pages, so blocks carry no page number."""

import io
import re
import zipfile

from docx import Document as open_docx
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.services.ingestion.blocks import HEADING, PARAGRAPH, Block, list_block, table_block
from app.services.ingestion.errors import UnreadableDocument
from app.services.ingestion.text import normalize_whitespace

_HEADING_STYLE = re.compile(r"^heading\s*(\d)$", re.IGNORECASE)


def parse_docx(data: bytes) -> list[Block]:
    try:
        document = open_docx(io.BytesIO(data))
    except (PackageNotFoundError, zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise UnreadableDocument("The Word file could not be read. It may be damaged; try saving it again.") from exc

    blocks: list[Block] = []
    pending_items: list[str] = []

    def flush_list():
        if pending_items:
            blocks.append(list_block(list(pending_items)))
            pending_items.clear()

    for element in document.element.body.iterchildren():
        if element.tag == qn("w:tbl"):
            flush_list()
            table = table_block(_table_rows(Table(element, document)))
            if table:
                blocks.append(table)
            continue
        if element.tag != qn("w:p"):
            continue
        paragraph = Paragraph(element, document)
        text = normalize_whitespace(paragraph.text)
        if not text:
            continue
        level = _heading_level(paragraph)
        if level is not None:
            flush_list()
            blocks.append(Block(HEADING, text, level=level))
        elif _is_list_item(paragraph):
            indent = "  " * _list_depth(paragraph)
            pending_items.append(f"{indent}- {text}")
        else:
            flush_list()
            blocks.append(Block(PARAGRAPH, text))
    flush_list()
    return blocks


def _style_name(paragraph: Paragraph) -> str:
    try:
        return (paragraph.style.name or "") if paragraph.style is not None else ""
    except (KeyError, ValueError):
        return ""


def _heading_level(paragraph: Paragraph) -> int | None:
    name = _style_name(paragraph)
    if name.lower() == "title":
        return 0
    match = _HEADING_STYLE.match(name)
    if match:
        return int(match.group(1))
    # A custom style can still be a heading through its outline level (0 = Heading 1).
    ppr = paragraph._p.pPr
    outline = ppr.find(qn("w:outlineLvl")) if ppr is not None else None
    if outline is not None:
        value = outline.get(qn("w:val"))
        if value is not None and value.isdigit() and int(value) < 9:
            return int(value) + 1
    return None


def _is_list_item(paragraph: Paragraph) -> bool:
    ppr = paragraph._p.pPr
    return (ppr is not None and ppr.numPr is not None) or _style_name(paragraph).lower().startswith("list")


def _list_depth(paragraph: Paragraph) -> int:
    ppr = paragraph._p.pPr
    if ppr is None or ppr.numPr is None or ppr.numPr.ilvl is None:
        return 0
    return min(int(ppr.numPr.ilvl.val or 0), 6)


def _table_rows(table: Table) -> list[list[str]]:
    rows = []
    for row in table.rows:
        cells, seen = [], set()
        for cell in row.cells:
            # A merged cell appears once per grid column it spans; keep it once.
            if id(cell._tc) in seen:
                continue
            seen.add(id(cell._tc))
            cells.append(normalize_whitespace(cell.text.replace("\n", " ")))
        rows.append(cells)
    return rows
