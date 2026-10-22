import io
import json
import urllib.error
from email.message import Message

import pytest

from app.services.ingestion import EmbeddingUnavailable, NoEmbedder
from app.services.ingestion.embeddings import BedrockEmbedder, build_embedder
from app.services.ingestion.openai_embeddings import OpenAIEmbedder


class FakeOpenAI:
    """urlopen for POST /embeddings: answers each input with a vector (in reverse order, as the
    API may), or with the queued errors first."""

    def __init__(self, dimensions=1024, errors=()):
        self.dimensions = dimensions
        self.errors = list(errors)
        self.requests = []

    def __call__(self, request, timeout):
        body = json.loads(request.data)
        self.requests.append((request, body))
        if self.errors:
            raise self.errors.pop(0)
        data = [{"index": i, "embedding": [float(i)] * self.dimensions} for i in range(len(body["input"]))]
        return io.BytesIO(json.dumps({"data": list(reversed(data))}).encode())


def http_error(code, message="", retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    body = json.dumps({"error": {"message": message}}).encode()
    return urllib.error.HTTPError("https://api.openai.com/v1/embeddings", code, "error", headers, io.BytesIO(body))


def embedder(api, **kwargs):
    return OpenAIEmbedder("sk-test", "text-embedding-3-small", urlopen=api, sleep=lambda _: None, **kwargs)


def test_asks_for_1024_dimensions_in_batches_and_keeps_the_order():
    api = FakeOpenAI()
    vectors = embedder(api).embed([f"passage {i}" for i in range(150)])

    assert [len(body["input"]) for _, body in api.requests] == [100, 50]
    request, body = api.requests[0]
    assert request.full_url == "https://api.openai.com/v1/embeddings"
    assert request.get_header("Authorization") == "Bearer sk-test"
    assert body["model"] == "text-embedding-3-small" and body["dimensions"] == 1024
    # Sorted back by "index": the second batch's first vector is input 0 of that batch.
    assert vectors[0][0] == 0.0 and vectors[99][0] == 99.0 and vectors[100][0] == 0.0
    assert len(vectors) == 150 and all(len(v) == 1024 for v in vectors)


def test_queries_are_embedded_the_same_way():
    api = FakeOpenAI()
    assert len(embedder(api).embed_query("What are the visa requirements?")) == 1024
    assert api.requests[0][1]["input"] == ["What are the visa requirements?"]


def test_rate_limits_and_server_errors_are_retried():
    api = FakeOpenAI(errors=[http_error(429, "Rate limit reached", retry_after=1), http_error(503)])
    waits = []
    vectors = OpenAIEmbedder("sk-test", "text-embedding-3-small", urlopen=api, sleep=waits.append).embed(["a"])
    assert len(vectors) == 1 and len(api.requests) == 3
    assert waits[0] == 1.0


def test_a_bad_key_fails_at_once_with_openais_reason():
    api = FakeOpenAI(errors=[http_error(401, "Incorrect API key provided")])
    with pytest.raises(EmbeddingUnavailable, match="401: Incorrect API key provided"):
        embedder(api).embed(["a"])
    assert len(api.requests) == 1


def test_giving_up_after_the_last_attempt():
    api = FakeOpenAI(errors=[urllib.error.URLError("timed out")] * 3)
    with pytest.raises(EmbeddingUnavailable):
        embedder(api, max_attempts=3).embed(["a"])
    assert len(api.requests) == 3


def test_vectors_of_the_wrong_size_are_refused():
    with pytest.raises(EmbeddingUnavailable, match="wrong size"):
        embedder(FakeOpenAI(dimensions=1536)).embed(["a"])


def test_a_missing_key_fails_the_document_not_the_startup():
    api = FakeOpenAI()
    with pytest.raises(EmbeddingUnavailable, match="OPENAI_API_KEY"):
        OpenAIEmbedder(None, "text-embedding-3-small", urlopen=api).embed(["a"])
    assert api.requests == []


def test_another_compatible_endpoint():
    api = FakeOpenAI()
    embedder(api, base_url="https://proxy.example.test/v1/").embed(["a"])
    assert api.requests[0][0].full_url == "https://proxy.example.test/v1/embeddings"


@pytest.mark.parametrize(
    ("settings", "kind", "model_id"),
    [
        ({"EMBEDDING_PROVIDER": "openai"}, OpenAIEmbedder, "text-embedding-3-small"),
        ({"EMBEDDING_PROVIDER": None}, OpenAIEmbedder, "text-embedding-3-small"),
        ({"EMBEDDING_PROVIDER": "openai", "EMBEDDING_MODEL_ID": "text-embedding-3-large"}, OpenAIEmbedder, "text-embedding-3-large"),
        ({"EMBEDDING_PROVIDER": "bedrock"}, BedrockEmbedder, "cohere.embed-multilingual-v3"),
        ({"EMBEDDING_PROVIDER": "Bedrock", "EMBEDDING_MODEL_ID": "amazon.titan-embed-text-v2:0"}, BedrockEmbedder, "amazon.titan-embed-text-v2:0"),
        ({"EMBEDDING_PROVIDER": "none"}, NoEmbedder, None),
    ],
)
def test_the_provider_and_model_come_from_the_settings(settings, kind, model_id):
    built = build_embedder({"OPENAI_API_KEY": "sk-test", **settings})
    assert isinstance(built, kind) and built.model_id == model_id


def test_an_unknown_provider_stops_startup():
    with pytest.raises(RuntimeError, match="openai, bedrock, none"):
        build_embedder({"EMBEDDING_PROVIDER": "cohere"})
