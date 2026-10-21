"""Plain text → blocks. Text files have no markup, so structure is inferred: paragraphs are
separated by blank lines, and a short standalone line is a heading when it looks like one
(underlined, numbered "2.1 Fees", "ARTICLE IV", "SECTION 3", ALL CAPS, or Title Case). Lines that
start with a bullet or number form lists; lines with | or tab separators form tables."""

import re

from app.services.ingestion.blocks import HEADING, PARAGRAPH, Block, list_block, table_block
from app.services.ingestion.text import normalize_whitespace

_UNDERLINE = re.compile(r"^(=+|-+)\s*$")
_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
_NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(\S.*)$")
_DIVISION_HEADING = re.compile(r"^(article|chapter|part|section|schedule|annex)\s+[\dIVXLCivxlc]+\b", re.IGNORECASE)
LIST_ITEM = re.compile(r"^\s*(?:[-*+•▪◦‣–]|\(?\d{1,3}[.)]|\(?[a-z][.)]|\([ivx]+\))\s+\S")
_TERMINAL = re.compile(r"[.,;:!?。]$")
_SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with", "sa", "ng", "und", "der", "die", "das"}


def parse_plain_text(text: str) -> list[Block]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n\n")
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    blocks: list[Block] = []
    for index, paragraph in enumerate(paragraphs):
        lines = [line.rstrip() for line in paragraph.strip("\n").split("\n") if line.strip()]
        followed = index + 1 < len(paragraphs)
        blocks.extend(_blocks_of(lines, followed))
    return blocks


def _blocks_of(lines: list[str], followed: bool) -> list[Block]:
    # "Title" underlined with === or --- (as in Markdown's setext headings).
    if len(lines) == 2 and _UNDERLINE.match(lines[1].strip()) and len(lines[0].split()) <= 15:
        return [Block(HEADING, lines[0].strip(), level=1 if lines[1].strip()[0] == "=" else 2)]
    if len(lines) == 1:
        heading = _heading(lines[0].strip(), followed)
        if heading:
            return [heading]
    if _is_table(lines):
        separator = "|" if all("|" in line for line in lines) else "\t"
        rows = [[c.strip() for c in line.strip().strip("|").split(separator)] for line in lines]
        rows = [row for row in rows if not all(re.fullmatch(r":?-{2,}:?", c) for c in row if c)]
        table = table_block(rows)
        return [table] if table else []
    if LIST_ITEM.match(lines[0]) and sum(bool(LIST_ITEM.match(line)) for line in lines) >= max(2, len(lines) // 2):
        items: list[str] = []
        for line in lines:
            if LIST_ITEM.match(line) or not items:
                items.append(normalize_whitespace(line))
            else:
                items[-1] += " " + normalize_whitespace(line)
        return [list_block(items)]
    return [Block(PARAGRAPH, join_lines(lines))]


def _heading(line: str, followed: bool) -> Block | None:
    words = line.split()
    if not followed or not words or len(words) > 12 or len(line) > 120:
        return None
    markdown = _MARKDOWN_HEADING.match(line)
    if markdown:
        return Block(HEADING, markdown.group(2), level=len(markdown.group(1)))
    if _TERMINAL.search(line) and not line.endswith(":"):
        return None
    if _DIVISION_HEADING.match(line):
        level = 2 if line.lower().startswith(("section", "schedule", "annex")) else 1
        return Block(HEADING, line.rstrip(":"), level=level)
    numbered = _NUMBERED_HEADING.match(line)
    if numbered and len(words) <= 10 and numbered.group(2)[0].isupper():
        return Block(HEADING, line, level=min(numbered.group(1).count(".") + 1, 4))
    letters = [c for c in line if c.isalpha()]
    if len(letters) >= 3 and all(c.isupper() for c in letters):
        return Block(HEADING, line.rstrip(":"), level=1)
    significant = [w for w in words if w.lower() not in _SMALL_WORDS and w[0].isalpha()]
    if len(words) <= 8 and significant and all(w[0].isupper() for w in significant) and not line.endswith(":"):
        return Block(HEADING, line, level=2)
    return None


def _is_table(lines: list[str]) -> bool:
    if len(lines) < 2:
        return False
    if all(line.count("|") >= 2 for line in lines):
        return True
    tabs = [line.strip().count("\t") for line in lines]
    return min(tabs) >= 1 and len(set(tabs)) == 1


def join_lines(lines: list[str]) -> str:
    """Hard-wrapped lines back into one paragraph, undoing hyphenation at line ends."""
    text = ""
    for line in lines:
        line = normalize_whitespace(line)
        if text.endswith("-") and line[:1].islower():
            text = text[:-1] + line
        else:
            text = f"{text} {line}" if text else line
    return text
