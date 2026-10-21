from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, login, make_app


def test_login_returns_a_token_that_works(client, admin_user):
    response = login(client, email="  Admin@Example.TEST ")

    assert response.status_code == 200
    body = response.get_json()
    assert body["user"]["email"] == ADMIN_EMAIL
    assert body["expiresAt"]
    me = client.get("/api/v1/admin/auth/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200 and me.get_json()["email"] == ADMIN_EMAIL
    assert me.headers["Cache-Control"] == "no-store"


def test_wrong_password_and_unknown_email_look_the_same(client, admin_user):
    wrong = login(client, password="not the password")
    unknown = login(client, email="nobody@example.test")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.get_json()["message"] == unknown.get_json()["message"] == "Incorrect email or password."


def test_logout_revokes_every_token(client, admin_user):
    first = {"Authorization": f"Bearer {login(client).get_json()['token']}"}
    second = {"Authorization": f"Bearer {login(client).get_json()['token']}"}

    assert client.post("/api/v1/admin/auth/logout", headers=first).status_code == 204

    assert client.get("/api/v1/admin/auth/me", headers=first).status_code == 401
    assert client.get("/api/v1/admin/auth/me", headers=second).status_code == 401
    assert login(client).status_code == 200


def test_lockout_after_repeated_failures(app, client, admin_user):
    for _ in range(app.config["ADMIN_MAX_FAILURES"]):
        assert login(client, password="wrong password!").status_code == 401

    locked = login(client)  # even the right password
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    assert "Too many attempts" in locked.get_json()["message"]


def test_lockout_counts_per_ip_across_accounts(app, client, admin_user):
    for i in range(app.config["ADMIN_MAX_FAILURES"]):
        login(client, email=f"guess{i}@example.test")
    assert login(client).status_code == 429


def test_forwarded_for_is_ignored_without_a_trusted_proxy(app, client, admin_user):
    # TRUSTED_PROXY_COUNT=0: a caller can't dodge the lockout with a new X-Forwarded-For each time.
    for i in range(app.config["ADMIN_MAX_FAILURES"]):
        login(client, email=f"guess{i}@example.test", headers={"X-Forwarded-For": f"10.0.0.{i}"})
    assert login(client, headers={"X-Forwarded-For": "10.0.0.99"}).status_code == 429


def test_lockout_counts_the_visitor_behind_a_trusted_proxy(tmp_path, aws):
    app = make_app(tmp_path, TRUSTED_PROXY_COUNT=1)
    app.test_cli_runner().invoke(args=["admin", "create", ADMIN_EMAIL, "--password", ADMIN_PASSWORD])
    client = app.test_client()

    for i in range(app.config["ADMIN_MAX_FAILURES"]):
        login(client, email=f"guess{i}@example.test", headers={"X-Forwarded-For": "203.0.113.7"})

    assert login(client, headers={"X-Forwarded-For": "203.0.113.7"}).status_code == 429
    # Another visitor behind the same proxy is not locked out.
    assert login(client, headers={"X-Forwarded-For": "198.51.100.2"}).status_code == 200


def test_deactivated_admin_is_signed_out(app, client, admin_headers):
    app.test_cli_runner().invoke(args=["admin", "deactivate", ADMIN_EMAIL])
    assert client.get("/api/v1/admin/auth/me", headers=admin_headers).status_code == 401
    assert login(client).status_code == 401


def test_set_password_signs_out_and_takes_effect(app, client, admin_headers):
    result = app.test_cli_runner().invoke(args=["admin", "set-password", ADMIN_EMAIL, "--password", "a brand new password"])
    assert result.exit_code == 0, result.output
    assert client.get("/api/v1/admin/auth/me", headers=admin_headers).status_code == 401
    assert login(client).status_code == 401
    assert login(client, password="a brand new password").status_code == 200


def test_short_passwords_are_refused(app):
    result = app.test_cli_runner().invoke(args=["admin", "create", "x@example.test", "--password", "short"])
    assert result.exit_code != 0 and "12 characters" in result.output


def test_login_without_secret_key_is_503(app, client, admin_user):
    app.config["SECRET_KEY"] = None
    assert login(client).status_code == 503


def test_cors_preflight_is_not_blocked_by_the_admin_guard(client):
    response = client.options(
        "/api/v1/admin/pages",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
    assert "Authorization" in response.headers["Access-Control-Allow-Headers"]


def test_password_is_not_echoed_in_validation_errors(client):
    response = client.post("/api/v1/admin/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "x": 1})
    assert response.status_code == 422
    assert ADMIN_PASSWORD not in response.get_data(as_text=True)


# ---------- Default admin (flask admin ensure-default) ----------


def test_default_admin_is_created_with_a_generated_password(app, client):
    output = app.test_cli_runner().invoke(args=["admin", "ensure-default"]).output

    assert "Created the default admin: admin@deszr.com" in output
    password = output.split("Password: ")[1].split()[0]
    assert len(password) >= 20
    assert login(client, email="admin@deszr.com", password=password).status_code == 200


def test_default_admin_uses_the_configured_email_and_password(app, client):
    app.config.update(DEFAULT_ADMIN_EMAIL="Owner@Example.test", DEFAULT_ADMIN_PASSWORD="a chosen password")
    output = app.test_cli_runner().invoke(args=["admin", "ensure-default"]).output

    assert "Created the default admin: owner@example.test" in output
    assert "Password:" not in output  # a password you chose is never printed
    assert login(client, email="owner@example.test", password="a chosen password").status_code == 200


def test_default_admin_is_never_created_twice_or_over_an_existing_admin(app, client, admin_user):
    runner = app.test_cli_runner()
    output = runner.invoke(args=["admin", "ensure-default"]).output
    assert "An admin account exists already" in output
    assert "admin@deszr.com" not in runner.invoke(args=["admin", "list"]).output
    # The existing admin's password is untouched.
    assert login(client).status_code == 200


def test_default_admin_can_be_turned_off_and_refuses_weak_passwords(app):
    runner = app.test_cli_runner()
    app.config["DEFAULT_ADMIN_EMAIL"] = ""
    assert "nothing to do" in runner.invoke(args=["admin", "ensure-default"]).output

    result = runner.invoke(args=["admin", "ensure-default", "--email", "a@example.test", "--password", "short"])
    assert result.exit_code != 0 and "12 characters" in result.output
    assert runner.invoke(args=["admin", "list"]).output.strip() == ""
