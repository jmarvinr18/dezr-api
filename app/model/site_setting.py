from sqlalchemy.dialects.postgresql import JSONB

from app.database.db import db
from app.constants import SETTINGS_SECTIONS
from app.model.admin_user import utcnow


def _in(column, values):
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


# JSONB on PostgreSQL, plain JSON elsewhere (SQLite in tests).
JsonDocument = db.JSON().with_variant(JSONB(), "postgresql")


class SiteSetting(db.Model):
    """One section of the site settings (site, contact, nav, ...) as an admin saved it.

    A section without a row shows the shipped default (app/seed/site_defaults.py), so
    deleting a row is how a section is reset.
    """

    __tablename__ = "site_settings"
    __table_args__ = (db.CheckConstraint(_in("section", SETTINGS_SECTIONS), name="ck_site_settings_section"),)

    section = db.Column(db.String(30), primary_key=True)
    value = db.Column(JsonDocument, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    updated_by = db.Column(db.String(255), nullable=False)
