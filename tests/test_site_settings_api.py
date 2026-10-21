import copy

from app.seed import SITE_DEFAULTS
from app.schema import SiteSettingsSchema


def wire_defaults():
    """The shipped settings as the API sends them (camelCase)."""
    return SiteSettingsSchema().dump(copy.deepcopy(SITE_DEFAULTS))


def test_public_settings_are_the_defaults_until_an_admin_saves(client):
    response = client.get("/api/v1/site-settings")

    assert response.status_code == 200
    body = response.get_json()
    assert body == wire_defaults()
    assert body["contact"]["addressLines"][0].startswith("7/F Electra House")
    assert body["contact"]["japanDeskEmail"] == "japandesk@deszr.com"
    assert [s["scene"] for s in body["slides"]][:2] == ["dusk", "night"]
    assert body["nav"][0] == {"label": "Home", "to": "/", "home": True}


def test_defaults_pass_their_own_validation():
    # What ships must be something an admin could save.
    SiteSettingsSchema().load(wire_defaults())


def test_admin_endpoints_need_a_token(client):
    assert client.get("/api/v1/admin/site-settings").status_code == 401
    bad = {"Authorization": "Bearer not-a-token"}
    assert client.get("/api/v1/admin/site-settings", headers=bad).status_code == 401
    assert client.put("/api/v1/admin/site-settings", json=wire_defaults()).status_code == 401


def test_put_replaces_settings_and_goes_live(client, admin_headers):
    settings = wire_defaults()
    settings["site"]["name"] = "  Donato & Zarate  "
    settings["slides"] = settings["slides"][:2]

    response = client.put("/api/v1/admin/site-settings", json=settings, headers=admin_headers)

    assert response.status_code == 200
    body = response.get_json()
    assert body["settings"]["site"]["name"] == "Donato & Zarate"  # trimmed
    assert body["revision"]["updatedBy"] == "admin@example.test"
    assert set(body["editedSections"]) == set(settings)
    assert body["defaults"] == wire_defaults()

    public = client.get("/api/v1/site-settings").get_json()
    assert public["site"]["name"] == "Donato & Zarate"
    # Whole sections are replaced, so a shorter list really is shorter.
    assert len(public["slides"]) == 2


def test_patch_changes_only_the_sections_sent(client, admin_headers):
    banners = wire_defaults()["banners"][:1]

    response = client.patch("/api/v1/admin/site-settings", json={"banners": banners}, headers=admin_headers)

    assert response.status_code == 200
    body = response.get_json()
    assert body["editedSections"] == ["banners"]
    assert body["settings"]["banners"] == banners
    assert body["settings"]["slides"] == wire_defaults()["slides"]


def test_patch_with_nothing_is_rejected(client, admin_headers):
    response = client.patch("/api/v1/admin/site-settings", json={}, headers=admin_headers)
    assert response.status_code == 422


def test_invalid_settings_report_every_field(client, admin_headers):
    settings = wire_defaults()
    settings["slides"][2]["title"] = "   "
    settings["slides"][0]["scene"] = "sunset"
    settings["nav"][1]["to"] = "javascript:alert(1)"
    settings["banners"][0]["lang"] = "English"

    response = client.put("/api/v1/admin/site-settings", json=settings, headers=admin_headers)

    assert response.status_code == 422
    errors = response.get_json()["errors"]["json"]
    assert errors["slides"]["2"]["title"] == ["Required."]
    assert "scene" in errors["slides"]["0"]
    assert errors["nav"]["1"]["to"] == ["Use a site path (/page) or a full https:// link."]
    assert "lang" in errors["banners"]["0"]
    # Nothing was saved.
    assert client.get("/api/v1/site-settings").get_json() == wire_defaults()


def test_put_needs_every_section(client, admin_headers):
    settings = wire_defaults()
    del settings["footer"]
    response = client.put("/api/v1/admin/site-settings", json=settings, headers=admin_headers)
    assert response.status_code == 422
    assert "footer" in response.get_json()["errors"]["json"]


def test_reset_one_section_then_all(client, admin_headers):
    settings = wire_defaults()
    settings["mission"]["title"] = "Why us"
    settings["assistant"]["greeting"] = "Hello"
    client.put("/api/v1/admin/site-settings", json=settings, headers=admin_headers)

    response = client.delete("/api/v1/admin/site-settings/mission", headers=admin_headers)
    assert response.status_code == 200
    body = response.get_json()
    assert body["settings"]["mission"]["title"] == "Our Mission"
    assert body["settings"]["assistant"]["greeting"] == "Hello"
    assert "mission" not in body["editedSections"]

    response = client.delete("/api/v1/admin/site-settings", headers=admin_headers)
    assert response.get_json()["revision"] is None
    assert client.get("/api/v1/site-settings").get_json() == wire_defaults()


def test_reset_unknown_section_is_404(client, admin_headers):
    assert client.delete("/api/v1/admin/site-settings/colours", headers=admin_headers).status_code == 404
