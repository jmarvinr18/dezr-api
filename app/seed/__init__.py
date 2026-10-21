"""Content the site ships with: the default site settings and the pages (copied from
dezr-ui/content). `flask seed-content` loads the pages into the database; both stay the
defaults an admin can reset to."""

from .pages import SeedPage, load_seed_pages, seed_page_for
from .site_defaults import SITE_DEFAULTS
