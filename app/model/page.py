from app.database.db import db
from app.constants import PAGE_SIDEBARS
from app.model.admin_user import utcnow
from app.model.site_setting import _in
import uuid


class Page(db.Model):
    """A content page of the site: its Markdown body and the options that shape its layout."""

    __tablename__ = "pages"
    __table_args__ = (db.CheckConstraint(_in("sidebar", PAGE_SIDEBARS), name="ck_pages_sidebar"),)

    id = db.Column(db.Uuid, primary_key=True, default=uuid.uuid4)
    # "/" for the home page, "/contact", ...
    path = db.Column(db.String(200), nullable=False, unique=True)
    title = db.Column(db.String(120), nullable=False)
    # Shown above the article; empty hides it (home page).
    heading = db.Column(db.String(120), nullable=False, default="")
    description = db.Column(db.String(300), nullable=False, default="")
    # Language of the body (Deutsch, Nihongo and Tagalog pages).
    lang = db.Column(db.String(10), nullable=False, default="en")
    slideshow = db.Column(db.Boolean, nullable=False, default=False)
    sidebar = db.Column(db.String(10), nullable=False, default="banners")
    # Print / email icons above the article.
    tools = db.Column(db.Boolean, nullable=False, default=True)
    # Extra breadcrumb between Home and the page.
    parent_title = db.Column(db.String(120))
    parent_to = db.Column(db.String(500))
    # Order in the site map.
    sort_order = db.Column(db.Integer, nullable=False, default=99)
    published = db.Column(db.Boolean, nullable=False, default=True)
    # Markdown with the site's components (::person-profile, ::library-grid, ...). Stored as
    # written: whoever renders it must sanitise it, as dezr-ui's renderMarkdown() does.
    body = db.Column(db.Text, nullable=False, default="")

    # The path of the shipped page (app/seed/pages) this row was seeded from, kept when an
    # admin moves the page, so it can still be reset. Null for pages an admin created.
    shipped_path = db.Column(db.String(200), unique=True)
    # Null while the page is exactly as shipped; the admin's email once someone changes it.
    updated_by = db.Column(db.String(255))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    @property
    def parent(self):
        if not self.parent_title:
            return None
        return {"title": self.parent_title, "to": self.parent_to}

    @property
    def edited(self):
        return self.updated_by is not None
