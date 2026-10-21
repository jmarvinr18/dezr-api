"""Markdown → blocks, using markdown-it's block structure. Each block keeps its Markdown source
(tables stay pipe tables, lists keep their markers), which reads well to a language model."""

import re

from markdown_it import MarkdownIt

from app.services.ingestion.blocks import CODE, HEADING, PARAGRAPH, QUOTE, TABLE, Block, list_block
from app.services.markdown_text import inline_text

_PARSER = MarkdownIt("commonmark").enable("table")
_FRONT_MATTER = re.compile(r"\A﻿?---\r?\n.*?\r?\n---[ \t]*\r?\n", re.DOTALL)
_HTML_TAG = re.compile(r"<[^>]+>")


def parse_markdown(text: str) -> list[Block]:
    text = _FRONT_MATTER.sub("", text).replace("\r\n", "\n")
    lines = text.split("\n")
    tokens = _PARSER.parse(text)

    def source(token) -> str:
        start, end = token.map
        return "\n".join(lines[start:end]).strip()

    blocks: list[Block] = []
    for i, token in enumerate(tokens):
        # Only top-level blocks; their source includes everything nested inside them.
        if token.level != 0 or token.map is None:
            continue
        kind = token.type
        if kind == "heading_open":
            title = inline_text(tokens[i + 1].content)
            if title:
                blocks.append(Block(HEADING, title, level=int(token.tag[1])))
        elif kind == "paragraph_open":
            blocks.append(Block(PARAGRAPH, source(token)))
        elif kind in ("bullet_list_open", "ordered_list_open"):
            items = [source(t) for t in _items_of(tokens, i)]
            blocks.append(list_block([item for item in items if item]))
        elif kind == "table_open":
            table = source(token).split("\n")
            header, rows = "\n".join(table[:2]), table[2:]
            blocks.append(Block(TABLE, source(token), parts=rows, header=header))
        elif kind in ("fence", "code_block"):
            blocks.append(Block(CODE, source(token)))
        elif kind == "blockquote_open":
            blocks.append(Block(QUOTE, source(token)))
        elif kind == "html_block":
            plain = _HTML_TAG.sub(" ", source(token)).strip()
            if plain:
                blocks.append(Block(PARAGRAPH, " ".join(plain.split())))
    return [b for b in blocks if b.text]


def _items_of(tokens, list_index: int):
    """The top-level items of the list opened at tokens[list_index]."""
    close = tokens[list_index].type.replace("_open", "_close")
    for token in tokens[list_index + 1:]:
        if token.level == 0 and token.type == close:
            return
        if token.type == "list_item_open" and token.level == 1 and token.map:
            yield token
