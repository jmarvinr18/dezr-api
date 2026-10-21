MESSAGE = {
    "name": "Juan dela Cruz",
    "email": "juan@example.com",
    "subject": "Incorporation",
    "message": "I would like to set up a company in Makati.",
    "copy": True,
}


def send(client, body=None, **kwargs):
    return client.post("/api/v1/contact-messages", json=body or MESSAGE, **kwargs)


def test_enquiry_is_stored_for_the_admin(client, admin_headers):
    response = send(client)

    assert response.status_code == 201
    receipt = response.get_json()
    assert set(receipt) == {"id", "receivedAt"}

    listed = client.get("/api/v1/admin/contact-messages", headers=admin_headers).get_json()
    assert listed["total"] == 1
    [item] = listed["items"]
    assert item["id"] == receipt["id"]
    assert (item["name"], item["email"], item["copy"]) == ("Juan dela Cruz", "juan@example.com", True)


def test_invalid_enquiry_is_422(client):
    response = send(client, {**MESSAGE, "email": "not-an-email", "message": "   "})
    assert response.status_code == 422
    assert {"email", "message"} <= set(response.get_json()["errors"]["json"])


def test_rate_limit_per_visitor(app, client):
    for _ in range(app.config["CONTACT_RATE_LIMIT_PER_HOUR"]):
        assert send(client).status_code == 201
    response = send(client)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "3600"


def test_ip_is_not_stored_in_clear(app, client):
    send(client)
    from app.model import ContactMessage

    with app.app_context():
        from app.database import db

        stored = db.session.query(ContactMessage).one()
        assert stored.client_ip_hash and "127.0.0.1" not in stored.client_ip_hash


def test_admin_can_read_page_and_delete(client, admin_headers):
    for i in range(3):
        send(client, {**MESSAGE, "subject": f"Question {i}"})

    page = client.get("/api/v1/admin/contact-messages?pageSize=2&page=2", headers=admin_headers).get_json()
    assert (page["total"], page["pageCount"], len(page["items"])) == (3, 2, 1)
    assert page["items"][0]["subject"] == "Question 0"  # newest first

    message_id = page["items"][0]["id"]
    assert client.get(f"/api/v1/admin/contact-messages/{message_id}", headers=admin_headers).status_code == 200
    assert client.delete(f"/api/v1/admin/contact-messages/{message_id}", headers=admin_headers).status_code == 204
    assert client.get(f"/api/v1/admin/contact-messages/{message_id}", headers=admin_headers).status_code == 404


def test_messages_need_an_admin(client):
    assert client.get("/api/v1/admin/contact-messages").status_code == 401
