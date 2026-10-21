def by_path(client, path):
    return client.get("/api/v1/pages/by-path", query_string={"path": path})


def admin_page(client, headers, path):
    pages = client.get("/api/v1/admin/pages", headers=headers).get_json()
    return next(p for p in pages if p["path"] == path)


def test_seeded_pages_are_listed_in_site_map_order(client, seeded):
    response = client.get("/api/v1/pages")

    assert response.status_code == 200
    pages = response.get_json()
    assert [p["path"] for p in pages] == [
        "/",
        "/partners-and-counsel",
        "/library",
        "/deutsch",
        "/nihongo",
        "/tagalog",
        "/contact",
        "/work-with-us",
        "/site-map",
    ]
    work = next(p for p in pages if p["path"] == "/work-with-us")
    assert work["parent"] == {"title": "About us", "to": None}


def test_page_by_path_returns_front_matter_and_markdown(client, seeded):
    home = by_path(client, "/").get_json()
    assert home["title"] == "Law Office in Metro Manila, Philippines"
    assert home["slideshow"] is True and home["sidebar"] == "home" and home["heading"] == ""
    assert home["body"].startswith("::div{.dz-lead}")

    nihongo = by_path(client, "/nihongo/").get_json()  # trailing slash is fine
    assert (nihongo["lang"], nihongo["heading"]) == ("ja", "日本語による情報")
    assert ":japan-desk-email" in nihongo["body"]

    contact = by_path(client, "/contact").get_json()
    assert contact["tools"] is False and contact["body"].strip() == "::contact-section\n::"


def test_unknown_or_malformed_paths_are_404(client, seeded):
    for path in ["/nope", "/Contact", "/contact.html", "contact", "/../etc"]:
        assert by_path(client, path).status_code == 404, path
    assert client.get("/api/v1/pages/by-path").status_code == 422


def test_create_edit_and_publish_a_page(client, seeded, admin_headers):
    response = client.post(
        "/api/v1/admin/pages",
        json={"path": "/careers", "title": "Careers", "body": "We are **hiring**.", "published": False},
        headers=admin_headers,
    )
    assert response.status_code == 201
    page = response.get_json()
    assert page["edited"] is True and page["hasDefault"] is False and page["updatedBy"] == "admin@example.test"
    # Unpublished pages stay off the public site.
    assert by_path(client, "/careers").status_code == 404

    response = client.patch(
        f"/api/v1/admin/pages/{page['id']}",
        json={"published": True, "parent": {"title": "About us"}, "order": 8},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.get_json()["default"] is None

    public = by_path(client, "/careers").get_json()
    assert public["body"] == "We are **hiring**." and public["parent"] == {"title": "About us", "to": None}


def test_page_validation(client, seeded, admin_headers):
    bad = client.post(
        "/api/v1/admin/pages",
        json={"path": "/About Us", "title": " ", "sidebar": "left", "parent": {"title": "x", "to": "javascript:x"}},
        headers=admin_headers,
    )
    assert bad.status_code == 422
    errors = bad.get_json()["errors"]["json"]
    assert {"path", "title", "sidebar", "parent"} <= set(errors)

    taken = client.post("/api/v1/admin/pages", json={"path": "/contact", "title": "Contact 2"}, headers=admin_headers)
    assert taken.status_code == 409
    assert taken.get_json()["errors"] == {"path": "This path is taken."}


def test_edit_then_reset_restores_the_shipped_page(client, seeded, admin_headers):
    library = admin_page(client, admin_headers, "/library")
    assert library["edited"] is False and library["hasDefault"] is True

    response = client.patch(
        f"/api/v1/admin/pages/{library['id']}",
        json={"title": "Resources", "path": "/resources", "body": "Coming soon."},
        headers=admin_headers,
    )
    detail = response.get_json()
    assert detail["edited"] is True
    assert detail["default"]["path"] == "/library" and detail["default"]["title"] == "Library"
    assert by_path(client, "/library").status_code == 404
    assert by_path(client, "/resources").get_json()["body"] == "Coming soon."

    response = client.post(f"/api/v1/admin/pages/{library['id']}/reset", headers=admin_headers)
    assert response.status_code == 200
    restored = response.get_json()
    assert (restored["path"], restored["title"], restored["edited"]) == ("/library", "Library", False)
    assert "::library-grid" in by_path(client, "/library").get_json()["body"]


def test_reseeding_keeps_admin_edits(app, client, seeded, admin_headers):
    contact = admin_page(client, admin_headers, "/contact")
    client.patch(f"/api/v1/admin/pages/{contact['id']}", json={"heading": "Reach us"}, headers=admin_headers)

    output = app.test_cli_runner().invoke(args=["seed-content"]).output
    assert "Kept (edited by an admin): /contact" in output
    assert by_path(client, "/contact").get_json()["heading"] == "Reach us"

    app.test_cli_runner().invoke(args=["seed-content", "--force"])
    assert by_path(client, "/contact").get_json()["heading"] == "Contact"


def test_home_page_is_protected(client, seeded, admin_headers):
    home = admin_page(client, admin_headers, "/")
    url = f"/api/v1/admin/pages/{home['id']}"
    assert client.delete(url, headers=admin_headers).status_code == 409
    assert client.patch(url, json={"published": False}, headers=admin_headers).status_code == 409
    assert client.patch(url, json={"path": "/home"}, headers=admin_headers).status_code == 409


def test_delete_page_and_reset_admin_page(client, seeded, admin_headers):
    tagalog = admin_page(client, admin_headers, "/tagalog")
    assert client.delete(f"/api/v1/admin/pages/{tagalog['id']}", headers=admin_headers).status_code == 204
    assert by_path(client, "/tagalog").status_code == 404

    created = client.post("/api/v1/admin/pages", json={"path": "/news", "title": "News"}, headers=admin_headers)
    response = client.post(f"/api/v1/admin/pages/{created.get_json()['id']}/reset", headers=admin_headers)
    assert response.status_code == 409


def test_admin_pages_need_a_token(client, seeded):
    assert client.get("/api/v1/admin/pages").status_code == 401
    assert client.post("/api/v1/admin/pages", json={"path": "/x", "title": "X"}).status_code == 401
