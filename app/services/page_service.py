"""Content pages: what visitors read, what admins edit, and the shipped versions they can go
back to (app/seed/pages)."""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from app.constants import PAGE_PATH_PATTERN, REQUIRED_PAGES
from app.database import db
from app.model import Page
from app.seed import SeedPage, load_seed_pages, seed_page_for
from app.services.errors import Conflict, NotFound

_SITE_ORDER = (Page.sort_order, Page.path)


def normalize_path(path: str) -> str:
    """"/contact/" → "/contact"; the home page stays "/"."""
    path = (path or "").strip()
    if not path.startswith("/"):
        return path
    return path.rstrip("/") or "/"


# ---------- Public ----------


def list_published() -> list[Page]:
    return list(db.session.scalars(select(Page).where(Page.published.is_(True)).order_by(*_SITE_ORDER)))


def get_published(path: str) -> Page:
    path = normalize_path(path)
    page = None
    if PAGE_PATH_PATTERN.match(path):
        page = db.session.scalar(select(Page).where(Page.path == path, Page.published.is_(True)))
    if page is None:
        raise NotFound("Page not found.")
    return page


# ---------- Admin ----------


def list_pages() -> list[Page]:
    return list(db.session.scalars(select(Page).order_by(*_SITE_ORDER)))


def get_page(page_id: uuid.UUID) -> Page:
    page = db.session.get(Page, page_id)
    if page is None:
        raise NotFound("Page not found.")
    return page


def default_for(page: Page) -> dict | None:
    """The shipped version of a page, as editable fields; None for pages admins created."""
    seed = seed_page_for(page.shipped_path) if page.shipped_path else None
    return _content(seed.fields()) if seed else None


def create_page(data: dict, created_by: str) -> Page:
    _assert_path_free(data["path"])
    page = Page(updated_by=created_by)
    _apply(page, data)
    db.session.add(page)
    db.session.commit()
    return page


def update_page(page: Page, data: dict, updated_by: str) -> Page:
    if "path" in data and data["path"] != page.path:
        if page.path in REQUIRED_PAGES:
            raise Conflict("The home page can't be moved.")
        _assert_path_free(data["path"], exclude=page.id)
    if data.get("published") is False and page.path in REQUIRED_PAGES:
        raise Conflict("The home page can't be unpublished.")
    _apply(page, data)
    page.updated_by = updated_by
    db.session.commit()
    return page


def delete_page(page: Page) -> None:
    if page.path in REQUIRED_PAGES:
        raise Conflict("The home page can't be deleted.")
    db.session.delete(page)
    db.session.commit()


def reset_page(page: Page) -> Page:
    """Restores the shipped version of a page (its text, options and path)."""
    seed = seed_page_for(page.shipped_path) if page.shipped_path else None
    if seed is None:
        raise Conflict("This page was created in the admin area; there is no shipped version to restore.")
    if seed.path != page.path:
        _assert_path_free(seed.path, exclude=page.id)
    _apply(page, seed.fields())
    page.updated_by = None
    db.session.commit()
    return page


# ---------- Seeding ----------


@dataclass
class SeedReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)


def seed_pages(force: bool = False) -> SeedReport:
    """Loads the shipped pages. Safe to run on every deploy:

    - a missing page is created;
    - a page nobody has edited is brought up to date with its shipped version;
    - a page an admin edited is kept, unless `force` (then it is reset);
    - a shipped page whose path an admin page already uses is reported as blocked.
    """
    report = SeedReport()
    for seed in load_seed_pages():
        page = db.session.scalar(select(Page).where(Page.shipped_path == seed.path))
        if page is None:
            if db.session.scalar(select(Page.id).where(Page.path == seed.path)):
                report.blocked.append(seed.path)
                continue
            page = Page(shipped_path=seed.path)
            _apply(page, seed.fields())
            db.session.add(page)
            report.created.append(seed.path)
        elif page.edited and not force:
            report.kept.append(page.path)
        elif _differs(page, seed):
            if seed.path != page.path and db.session.scalar(
                select(Page.id).where(Page.path == seed.path, Page.id != page.id)
            ):
                report.blocked.append(seed.path)
                continue
            _apply(page, seed.fields())
            page.updated_by = None
            report.updated.append(seed.path)
    db.session.commit()
    return report


# ---------- Helpers ----------


def _assert_path_free(path: str, exclude: uuid.UUID | None = None) -> None:
    query = select(Page.id).where(Page.path == path)
    if exclude is not None:
        query = query.where(Page.id != exclude)
    if db.session.scalar(query):
        raise Conflict(f"Another page already uses {path}.", errors={"path": "This path is taken."})


def _apply(page: Page, data: dict) -> None:
    """Copies validated fields onto the page. `parent` arrives as {title, to} or None."""
    data = dict(data)
    if "parent" in data:
        parent = data.pop("parent") or {}
        data["parent_title"] = parent.get("title")
        data["parent_to"] = parent.get("to") if parent else None
    for key, value in data.items():
        setattr(page, key, value)


def _content(fields_: dict) -> dict:
    """Seed fields in the shape PageContentSchema dumps."""
    content = dict(fields_)
    title, to = content.pop("parent_title"), content.pop("parent_to")
    content["parent"] = {"title": title, "to": to} if title else None
    return content


def _differs(page: Page, seed: SeedPage) -> bool:
    return any(getattr(page, key) != value for key, value in seed.fields().items())
