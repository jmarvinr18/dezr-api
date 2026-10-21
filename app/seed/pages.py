"""The pages the site ships with: one Markdown file per page in app/seed/pages, with the same
front matter as dezr-ui's content/*.md (title, heading, description, lang, slideshow,
sidebar, tools, parent, order)."""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

SEED_DIR = Path(__file__).resolve().parent / "pages"

_FRONT_MATTER = re.compile(r"\A﻿?---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


@dataclass(frozen=True)
class SeedPage:
    path: str
    title: str
    heading: str
    description: str
    lang: str
    slideshow: bool
    sidebar: str
    tools: bool
    parent_title: str | None
    parent_to: str | None
    sort_order: int
    body: str

    def fields(self) -> dict:
        """The page's columns, as the service writes them."""
        return {
            "path": self.path,
            "title": self.title,
            "heading": self.heading,
            "description": self.description,
            "lang": self.lang,
            "slideshow": self.slideshow,
            "sidebar": self.sidebar,
            "tools": self.tools,
            "parent_title": self.parent_title,
            "parent_to": self.parent_to,
            "sort_order": self.sort_order,
            "published": True,
            "body": self.body,
        }


def path_for(file: Path) -> str:
    """index.md → "/", partners-and-counsel.md → "/partners-and-counsel"."""
    return "/" if file.stem == "index" else f"/{file.stem}"


def parse_seed_page(path: str, text: str) -> SeedPage:
    match = _FRONT_MATTER.match(text)
    meta = (yaml.safe_load(match.group(1)) if match else None) or {}
    body = text[match.end():] if match else text
    parent = meta.get("parent") or {}
    return SeedPage(
        path=path,
        title=str(meta.get("title") or path),
        heading=str(meta.get("heading") or ""),
        description=str(meta.get("description") or ""),
        lang=str(meta.get("lang") or "en"),
        slideshow=bool(meta.get("slideshow", False)),
        sidebar=str(meta.get("sidebar") or "banners"),
        tools=bool(meta.get("tools", True)),
        parent_title=parent.get("title"),
        parent_to=parent.get("to"),
        sort_order=int(meta.get("order", 99)),
        body=body.lstrip("\r\n"),
    )


@lru_cache(maxsize=1)
def load_seed_pages() -> tuple[SeedPage, ...]:
    files = sorted(SEED_DIR.glob("*.md"))
    return tuple(parse_seed_page(path_for(f), f.read_text(encoding="utf-8")) for f in files)


def seed_page_for(path: str) -> SeedPage | None:
    return next((p for p in load_seed_pages() if p.path == path), None)
