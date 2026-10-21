"""PDF → blocks, with pdfplumber. A PDF has no markup, only positioned text, so structure is
read from the layout:

- **Tables**: found from their ruling lines; kept whole as pipe tables, and their text is left
  out of the surrounding paragraphs.
- **Headings**: lines set larger than the body text (each larger size is one level up), or a
  short line set entirely in bold.
- **Paragraphs and lists**: lines run together until a vertical gap; a line starting with a
  bullet or number starts a list item. Words hyphenated across lines are joined again.
- **Running headers, footers and page numbers**: lines in the top or bottom margin that repeat
  on most pages are dropped.

Scanned PDFs have no text layer and are refused (OCR is not done here). Multi-column layouts
are read line by line across the columns.
"""

import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import median

import pdfplumber

from app.services.ingestion.blocks import HEADING, PARAGRAPH, Block, list_block, table_block
from app.services.ingestion.errors import UnreadableDocument
from app.services.ingestion.parsers.plain_text import LIST_ITEM, join_lines

NO_TEXT = "The PDF has no readable text. It may be a scan; upload a PDF with selectable text."
_TERMINAL = re.compile(r"[.!?;。]$")
_PAGE_NUMBER = re.compile(r"^(page\s*)?#(\s*(of|/)\s*#)?$|^[-–]\s*#\s*[-–]$")
_MARGIN = 0.08


@dataclass
class _Line:
    text: str
    page: int
    top: float
    bottom: float
    x0: float
    size: float
    bold: bool
    page_height: float

    @property
    def height(self) -> float:
        return max(self.bottom - self.top, 1.0)


def parse_pdf(data: bytes) -> list[Block]:
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
        pages = pdf.pages
    except Exception as exc:
        if _is_password_error(exc):
            raise UnreadableDocument("The PDF is password-protected. Upload a copy without a password.") from exc
        raise UnreadableDocument("The PDF could not be read. It may be damaged; try saving it again.") from exc

    items: list[tuple[int, float, object]] = []
    with pdf:
        for number, page in enumerate(pages, 1):
            tables, boxes = _tables(page, number)
            for table, top in tables:
                items.append((number, top, table))
            for line in _lines(page, number, boxes):
                items.append((number, line.top, line))
            page.close()
        page_count = len(pages)

    lines = [item for _, _, item in items if isinstance(item, _Line)]
    furniture = _furniture(lines, page_count)
    lines = [line for line in lines if id(line) not in furniture]
    if not lines and not any(isinstance(item, Block) for _, _, item in items):
        raise UnreadableDocument(NO_TEXT)

    level_of = _heading_levels(lines)
    ordered = sorted(
        (item for item in items if not (isinstance(item[2], _Line) and id(item[2]) in furniture)),
        key=lambda item: (item[0], item[1]),
    )
    blocks = _assemble([item for _, _, item in ordered], level_of)
    if not blocks:
        raise UnreadableDocument(NO_TEXT)
    return blocks


def _tables(page, number: int):
    """The page's tables as (block, top) pairs, and their bounding boxes."""
    try:
        found = page.find_tables()
    except Exception:
        return [], []
    tables = []
    for table in found:
        block = table_block(table.extract(), page=number)
        if block:
            tables.append((block, table.bbox[1]))
    return tables, [t.bbox for t in found]


def _lines(page, number: int, boxes: list) -> list[_Line]:
    """The page's text lines, leaving out the words inside tables (`boxes`)."""

    def outside_tables(obj) -> bool:
        if obj.get("object_type") != "char":
            return True
        cx, cy = (obj["x0"] + obj["x1"]) / 2, (obj["top"] + obj["bottom"]) / 2
        return not any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in boxes)

    source = page.filter(outside_tables) if boxes else page
    lines = []
    for line in source.extract_text_lines(return_chars=True, strip=True):
        chars = [c for c in line.get("chars", []) if c.get("text", "").strip()]
        text = " ".join(line.get("text", "").split())
        if not chars or not text:
            continue
        bold = sum(_is_bold(c.get("fontname", "")) for c in chars) > len(chars) / 2
        lines.append(
            _Line(
                text=text,
                page=number,
                top=float(line["top"]),
                bottom=float(line["bottom"]),
                x0=float(line["x0"]),
                size=float(median(c.get("size", 0) for c in chars)),
                bold=bold,
                page_height=float(page.height),
            )
        )
    return lines


def _is_bold(fontname: str) -> bool:
    name = fontname.lower()
    return any(mark in name for mark in ("bold", "black", "heavy", "semibold", ",b"))


def _is_password_error(exc: BaseException) -> bool:
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if "password" in type(exc).__name__.lower() or "password" in str(exc).lower():
            return True
        exc = exc.__cause__ or exc.__context__ or next((a for a in exc.args if isinstance(a, BaseException)), None)
    return False


def _furniture(lines: list[_Line], page_count: int) -> set[int]:
    """Running headers, footers and page numbers: margin lines repeated on most pages."""

    def key(line: _Line) -> str:
        return re.sub(r"\d+", "#", line.text.lower()).strip()

    margin = [
        line for line in lines
        if line.top < line.page_height * _MARGIN or line.bottom > line.page_height * (1 - _MARGIN)
    ]
    pages_by_key: dict[str, set[int]] = defaultdict(set)
    for line in margin:
        pages_by_key[key(line)].add(line.page)
    repeated = {k for k, pages in pages_by_key.items() if page_count >= 2 and len(pages) >= max(2, page_count * 0.5)}
    return {id(line) for line in margin if key(line) in repeated or _PAGE_NUMBER.match(key(line))}


def _heading_levels(lines: list[_Line]):
    """A function giving a line's heading level, or None for body text."""
    weights = Counter()
    for line in lines:
        weights[round(line.size, 1)] += len(line.text)
    body = weights.most_common(1)[0][0] if weights else 0

    def short(line: _Line) -> bool:
        return len(line.text.split()) <= 16 and any(c.isalpha() for c in line.text)

    def large(line: _Line) -> bool:
        return body > 0 and line.size >= body * 1.15 and short(line)

    sizes = sorted({round(line.size * 2) / 2 for line in lines if large(line)}, reverse=True)[:4]

    def level_of(line: _Line) -> int | None:
        if large(line):
            rounded = round(line.size * 2) / 2
            return sizes.index(rounded) + 1 if rounded in sizes else len(sizes)
        if line.bold and short(line) and len(line.text.split()) <= 12 and not _TERMINAL.search(line.text) \
                and line.size >= body * 0.95 and not LIST_ITEM.match(line.text):
            return len(sizes) + 1
        return None

    return level_of


def _assemble(items: list, level_of) -> list[Block]:
    blocks: list[Block] = []
    group: list[_Line] = []
    items_of_list: list[list[_Line]] = []

    def flush():
        if items_of_list:
            entries = [join_lines([l.text for l in item]) for item in items_of_list]
            blocks.append(list_block(entries, page=items_of_list[0][0].page))
            items_of_list.clear()
        if group:
            blocks.append(Block(PARAGRAPH, join_lines([l.text for l in group]), page=group[0].page))
            group.clear()

    previous: _Line | None = None
    for item in items:
        if isinstance(item, Block):
            flush()
            blocks.append(item)
            previous = None
            continue
        line: _Line = item
        level = level_of(line)
        if level is not None:
            flush()
            last = blocks[-1] if blocks else None
            # A heading set over two lines is one heading.
            if (last is not None and last.kind == HEADING and last.level == level and previous is not None
                    and previous.page == line.page and line.top - previous.bottom < line.height):
                last.text = f"{last.text} {line.text}"
            else:
                blocks.append(Block(HEADING, line.text, level=level, page=line.page))
            previous = line
            continue

        if previous is not None and _starts_new_block(previous, line):
            flush()
        if LIST_ITEM.match(line.text):
            if group:
                flush()
            items_of_list.append([line])
        elif items_of_list and line.x0 > items_of_list[-1][0].x0 + 1:
            items_of_list[-1].append(line)  # an item's indented continuation line
        else:
            if items_of_list:
                flush()
            group.append(line)
        previous = line
    flush()
    return [b for b in blocks if b.text.strip()]


def _starts_new_block(previous: _Line, line: _Line) -> bool:
    if line.page != previous.page:
        # A sentence running onto the next page continues the paragraph.
        return bool(_TERMINAL.search(previous.text)) or not line.text[:1].islower()
    return line.top - previous.bottom > max(2.0, previous.height * 0.7)
