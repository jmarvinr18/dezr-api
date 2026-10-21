from pathlib import Path

from app.schema import PageCreateSchema
from app.seed import load_seed_pages
from app.seed.pages import parse_seed_page

UI_CONTENT = Path(__file__).resolve().parents[2] / "dezr-ui" / "content"


def test_every_shipped_page_loads_with_its_front_matter():
    pages = {p.path: p for p in load_seed_pages()}

    assert len(pages) == 9
    assert pages["/"].slideshow and pages["/"].sidebar == "home" and pages["/"].heading == ""
    assert pages["/deutsch"].lang == "de" and pages["/deutsch"].sort_order == 4
    assert (pages["/work-with-us"].parent_title, pages["/work-with-us"].parent_to) == ("About us", None)
    assert pages["/site-map"].tools is False
    assert not pages["/library"].body.startswith("---")


def test_shipped_pages_pass_admin_validation():
    # An admin must be able to save any shipped page unchanged.
    for page in load_seed_pages():
        fields = page.fields()
        payload = {
            "path": fields["path"],
            "title": fields["title"],
            "heading": fields["heading"],
            "description": fields["description"],
            "lang": fields["lang"],
            "slideshow": fields["slideshow"],
            "sidebar": fields["sidebar"],
            "tools": fields["tools"],
            "parent": {"title": fields["parent_title"], "to": fields["parent_to"]} if fields["parent_title"] else None,
            "order": fields["sort_order"],
            "body": fields["body"],
        }
        PageCreateSchema().load(payload)


def test_parses_pages_without_front_matter():
    page = parse_seed_page("/x", "Just text.\n")
    assert (page.title, page.body, page.sidebar, page.tools) == ("/x", "Just text.\n", "banners", True)


def test_seed_pages_match_the_ui_content_folder():
    """app/seed/pages is a copy of dezr-ui/content; flag drift when both are checked out."""
    if not UI_CONTENT.is_dir():
        return
    shipped = {p.name: p.read_text(encoding="utf-8") for p in (Path(__file__).resolve().parents[1] / "app" / "seed" / "pages").glob("*.md")}
    ui = {p.name: p.read_text(encoding="utf-8") for p in UI_CONTENT.glob("*.md")}
    assert shipped == ui
