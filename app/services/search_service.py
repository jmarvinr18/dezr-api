"""Site search over the published pages, section by section.

The site has a handful of pages, so they're matched in Python rather than with database
full-text search; that also keeps it working the same on PostgreSQL and SQLite.
"""

from app.services import page_service
from app.services.markdown_text import sections

MAX_RESULTS = 50


def search(query: str) -> list[dict]:
    """Sections whose page title, headings or text contain every word of the query."""
    terms = [t for t in query.lower().split() if t]
    if not terms:
        return []
    results = []
    for page in page_service.list_published():
        for section in sections(page.path, page.title, page.body):
            haystack = f"{section['title']} {' '.join(section['titles'])} {section['content']}".lower()
            if all(term in haystack for term in terms):
                results.append(section)
                if len(results) == MAX_RESULTS:
                    return results
    return results
