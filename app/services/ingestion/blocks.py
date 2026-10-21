"""The structure every parser produces, whatever the file format: a flat list of blocks in
reading order. Headings carry a level; the chunker turns them into each passage's heading path."""

from dataclasses import dataclass, field

HEADING = "heading"
PARAGRAPH = "paragraph"
LIST = "list"
TABLE = "table"
CODE = "code"
QUOTE = "quote"


@dataclass
class Block:
    kind: str
    text: str
    # Heading level: 0 for a document title, 1 for the top heading level, 2 below it, ...
    level: int = 0
    # 1-based page the block starts on (PDF only).
    page: int | None = None
    # List items or table rows, so an oversized list or table can be cut between them.
    parts: list[str] = field(default_factory=list)
    # A table's header row(s), repeated at the top of every piece of a split table.
    header: str = ""


def list_block(items: list[str], page: int | None = None) -> Block:
    return Block(LIST, "\n".join(items), page=page, parts=list(items))


def table_block(rows: list[list[str]], page: int | None = None) -> Block | None:
    """A table as a Markdown pipe table, first row as header. None for an empty table; a
    one-row "table" (a layout box) becomes a paragraph."""

    def cell(value) -> str:
        return " ".join(str(value or "").split()).replace("|", "\\|")

    rows = [[cell(c) for c in row] for row in rows]
    rows = [row for row in rows if any(row)]
    if not rows:
        return None
    if len(rows) == 1:
        return Block(PARAGRAPH, " ".join(c for c in rows[0] if c), page=page)
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]

    def line(row: list[str]) -> str:
        return "| " + " | ".join(row) + " |"

    header = line(rows[0]) + "\n" + "| " + " | ".join(["---"] * width) + " |"
    body = [line(row) for row in rows[1:]]
    return Block(TABLE, header + "\n" + "\n".join(body), page=page, parts=body, header=header)
