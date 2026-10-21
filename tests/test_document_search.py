"""POST /api/v1/documents/search: the AI assistant's retrieval over the documents' passages.

The key checks run on any database. The searches themselves need PostgreSQL with pgvector:
TEST_DATABASE_URL=postgresql://dezr:...@localhost:5435/dezr_test uv run pytest
"""

import pytest
from sqlalchemy import update

from app.constants import EMBEDDING_DIMENSIONS
from app.database import db
from app.model import Document
from app.services.ingestion import Embedder, EmbeddingUnavailable
from tests.conftest import TEST_DATABASE_URL, make_app
from tests.test_documents_api import process, upload

URL = "/api/v1/documents/search"
KEY = "test-search-key-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {KEY}"}

needs_postgres = pytest.mark.skipif(not TEST_DATABASE_URL, reason="search needs PostgreSQL (TEST_DATABASE_URL)")


class TopicEmbedder(Embedder):
    """Vectors by topic: a text about refunds and one about offices point in different
    directions, so a query can be near a passage without sharing any of its words."""

    model_id = "topic-embed-v1"
    TOPICS = {"refund": 1, "money back": 1, "office": 2}

    def __init__(self):
        self.query_fails = False

    def embed(self, texts):
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        if self.query_fails:
            raise EmbeddingUnavailable("embedding service down")
        return self._vector(text)

    def _vector(self, text):
        axis = next((n for word, n in self.TOPICS.items() if word in text.lower()), 0)
        vector = [0.0] * EMBEDDING_DIMENSIONS
        vector[axis] = 1.0
        return vector


@pytest.fixture
def search_app(tmp_path, aws):
    embedder = TopicEmbedder()
    app = make_app(tmp_path, embedder=embedder, DOCUMENT_SEARCH_API_KEY=KEY)
    app.embedder = embedder
    return app


@pytest.fixture
def search_client(search_app):
    return search_app.test_client()


@pytest.fixture
def documents(search_app, search_client, admin_headers_for):
    headers = admin_headers_for(search_client)
    refunds = upload(search_client, headers, "refunds.md",
                     b"# Refund policy\n\nRefunds are paid within 30 days of delivery.", title="Refund policy")
    offices = upload(search_client, headers, "profile.md",
                     b"# Company profile\n\nOur main office is in Makati City.", title="Company profile")
    assert process(search_app) == 2
    return {"refunds": refunds.get_json()["id"], "offices": offices.get_json()["id"]}


@pytest.fixture
def admin_headers_for():
    from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, login

    def headers(client):
        result = client.application.test_cli_runner().invoke(
            args=["admin", "create", ADMIN_EMAIL, "--password", ADMIN_PASSWORD])
        assert result.exit_code == 0, result.output
        return {"Authorization": f"Bearer {login(client).get_json()['token']}"}

    return headers


def search(client, query, **kwargs):
    return client.post(URL, json={"query": query, **kwargs}, headers=AUTH)


# ---------- the key ----------


def test_search_is_off_without_a_key(client):
    response = client.post(URL, json={"query": "refunds"}, headers=AUTH)
    assert response.status_code == 503
    assert "DOCUMENT_SEARCH_API_KEY" in response.get_json()["message"]


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": KEY}])
def test_search_needs_the_key(search_client, headers):
    response = search_client.post(URL, json={"query": "refunds"}, headers=headers)
    assert response.status_code == 401


def test_the_key_is_checked_before_the_body(search_client):
    assert search_client.post(URL, json={}, headers={}).status_code == 401


def test_an_admin_token_is_not_the_search_key(search_client, admin_headers_for):
    response = search_client.post(URL, json={"query": "refunds"}, headers=admin_headers_for(search_client))
    assert response.status_code == 401


@pytest.mark.parametrize("body", [{}, {"query": "   "}, {"query": "x" * 1001}, {"query": "x", "limit": 21}])
def test_invalid_searches_are_422(search_client, body):
    assert search_client.post(URL, json=body, headers=AUTH).status_code == 422


@pytest.mark.skipif(bool(TEST_DATABASE_URL), reason="checks the SQLite answer")
def test_sqlite_answers_that_search_needs_postgres(search_client):
    response = search(search_client, "refunds")
    assert response.status_code == 503
    assert "PostgreSQL" in response.get_json()["message"]


# ---------- searching (PostgreSQL) ----------


@needs_postgres
def test_vectors_find_a_passage_without_shared_words(search_client, documents):
    response = search(search_client, "how do I get my money back")

    assert response.status_code == 200
    body = response.get_json()
    assert body["mode"] == "hybrid"
    first = body["items"][0]
    assert first["documentId"] == documents["refunds"]
    assert first["title"] == "Refund policy"
    assert "30 days" in first["content"]
    assert set(first) == {"documentId", "chunkId", "title", "fileName", "headingPath",
                          "pageStart", "pageEnd", "content", "score"}
    assert response.headers["Cache-Control"] == "no-store"


@needs_postgres
def test_keywords_lift_exact_names(search_client, documents):
    # "Makati" has no topic, so its vector is no nearer one passage than the other; the keyword
    # match puts the office passage first
    items = search(search_client, "Makati").get_json()["items"]
    assert items[0]["documentId"] == documents["offices"]


@needs_postgres
def test_keywords_only_when_the_query_cannot_be_embedded(search_app, search_client, documents):
    search_app.embedder.query_fails = True
    body = search(search_client, "Makati").get_json()

    assert body["mode"] == "keyword"
    assert [i["documentId"] for i in body["items"]] == [documents["offices"]]


@needs_postgres
def test_keywords_find_passages_with_some_of_the_words(search_app, search_client, documents):
    # No passage has "opening" or "hours"; the one with "Makati" is still found
    search_app.embedder.query_fails = True
    items = search(search_client, "Makati opening hours").get_json()["items"]
    assert [i["documentId"] for i in items] == [documents["offices"]]


@needs_postgres
def test_only_ready_documents_embedded_with_the_current_model(search_app, search_client, documents):
    with search_app.app_context():
        db.session.execute(update(Document).where(Document.id == documents["refunds"]).values(embedding_model="old-model"))
        db.session.commit()
    ids = [i["documentId"] for i in search(search_client, "money back").get_json()["items"]]
    assert documents["refunds"] not in ids

    with search_app.app_context():
        db.session.execute(update(Document).where(Document.id == documents["offices"]).values(status="failed"))
        db.session.commit()
    ids = [i["documentId"] for i in search(search_client, "Makati").get_json()["items"]]
    assert documents["offices"] not in ids


@needs_postgres
def test_limit(search_client, documents):
    assert len(search(search_client, "office refund", limit=1).get_json()["items"]) == 1
