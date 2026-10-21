from app.services.markdown_text import sections, slugify_heading, to_text


def search(client, q):
    return client.get("/api/v1/search", query_string={"q": q}).get_json()


def test_finds_sections_by_every_word(client, seeded):
    results = search(client, "trademarks copyrights")

    ids = [r["id"] for r in results]
    assert "/#intellectual-property" in ids
    home = next(r for r in results if r["id"] == "/#intellectual-property")
    assert home["title"] == "Intellectual Property"
    assert home["titles"] == ["Law Office in Metro Manila, Philippines"]
    assert home["level"] == 2
    assert "**" not in home["content"]


def test_searches_other_languages_and_profile_names(client, seeded):
    assert any(r["id"].startswith("/nihongo#") for r in search(client, "知的財産"))
    assert any(r["id"] == "/partners-and-counsel#partners" for r in search(client, "partner name"))


def test_empty_query_returns_nothing(client, seeded):
    assert search(client, "   ") == []


def test_unpublished_pages_are_not_searched(client, seeded, admin_headers):
    client.post(
        "/api/v1/admin/pages",
        json={"path": "/draft", "title": "Draft", "body": "zebra crossing", "published": False},
        headers=admin_headers,
    )
    assert search(client, "zebra") == []


def test_component_markup_and_props_are_not_text():
    markdown = """
::div{.dz-lead}
We are a **general-practice** firm. See [the library](/library).
::

---

::library-grid
---
topics:
  - label: Tax
    url: https://example.com
---
::

Email us: :japan-desk-email at 10:30.

<!-- hidden note -->
"""
    text = to_text(markdown)
    assert text == "We are a general-practice firm. See the library. Email us:  at 10:30."
    assert "topics" not in text and "hidden" not in text


def test_sections_follow_heading_levels():
    found = sections("/x", "Page", "Intro\n\n## A\n\none\n\n### A.1\n\ntwo\n\n## B\n\nthree")
    assert [(s["id"], s["titles"], s["content"]) for s in found] == [
        ("/x", [], "Intro"),
        ("/x#a", ["Page"], "one"),
        ("/x#a1", ["Page", "A"], "two"),
        ("/x#b", ["Page"], "three"),
    ]


def test_heading_anchor_matches_the_site():
    assert slugify_heading("Banking & Finance") == "banking--finance"
    assert slugify_heading("業務内容") == "業務内容"
