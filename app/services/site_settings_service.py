"""Site settings: the shipped defaults (app/seed/site_defaults.py) with the sections an admin
saved laid over them. Sections are replaced whole, never merged, so a shorter list (fewer
slides, fewer links) really is shorter."""

import copy

from sqlalchemy import select

from app.constants import SETTINGS_SECTIONS
from app.database import db
from app.model import SiteSetting
from app.seed import SITE_DEFAULTS
from app.services.errors import NotFound, ValidationFailed


def _saved() -> dict[str, SiteSetting]:
    return {row.section: row for row in db.session.scalars(select(SiteSetting))}


def defaults() -> dict:
    return copy.deepcopy(SITE_DEFAULTS)


def current_settings() -> dict:
    """What the site shows: each saved section, or its default until an admin saves it."""
    settings = defaults()
    for section, row in _saved().items():
        settings[section] = row.value
    return settings


def admin_view() -> dict:
    saved = _saved()
    latest = max(saved.values(), key=lambda row: row.updated_at, default=None)
    settings = defaults()
    for section, row in saved.items():
        settings[section] = row.value
    return {
        "settings": settings,
        "defaults": defaults(),
        "revision": {"updated_at": latest.updated_at, "updated_by": latest.updated_by} if latest else None,
        "edited_sections": [s for s in SETTINGS_SECTIONS if s in saved],
    }


def save_sections(values: dict, updated_by: str) -> dict:
    """Replaces each section in `values` (already validated by SiteSettingsSchema)."""
    if not values:
        raise ValidationFailed("Send at least one section to change.")
    saved = _saved()
    for section, value in values.items():
        row = saved.get(section)
        if row is None:
            db.session.add(SiteSetting(section=section, value=value, updated_by=updated_by))
        else:
            row.value = value
            row.updated_by = updated_by
    db.session.commit()
    return admin_view()


def reset(sections: list[str] | None = None) -> dict:
    """Back to the shipped settings: every section, or only the ones named."""
    for section in sections or []:
        if section not in SETTINGS_SECTIONS:
            raise NotFound(f"Unknown settings section '{section}'.")
    query = SiteSetting.__table__.delete()
    if sections:
        query = query.where(SiteSetting.section.in_(sections))
    db.session.execute(query)
    db.session.commit()
    return admin_view()
