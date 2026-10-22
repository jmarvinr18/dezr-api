import io
import json

import pytest

from app.services.ingestion import EmbeddingUnavailable
from app.services.ingestion.embeddings import BedrockEmbedder


class FakeBedrock:
    """bedrock-runtime's invoke_model, answering like Cohere Embed v3 or Titan V2."""

    def __init__(self, dimensions=1024):
        self.dimensions = dimensions
        self.calls = []

    def invoke_model(self, modelId, body, contentType, accept):
        request = json.loads(body)
        self.calls.append((modelId, request))
        if modelId.startswith("cohere."):
            answer = {"embeddings": [[0.1] * self.dimensions for _ in request["texts"]], "response_type": "embeddings_floats"}
        else:
            answer = {"embedding": [0.1] * self.dimensions}
        return {"body": io.BytesIO(json.dumps(answer).encode())}


def test_cohere_texts_are_clipped_to_its_character_limit():
    client = FakeBedrock()
    embedder = BedrockEmbedder(client, "cohere.embed-multilingual-v3")
    long_passage = "Title\nHeading\n\n" + "x" * 5000

    vectors = embedder.embed([long_passage, "short"])

    [(model, request)] = client.calls
    assert model == "cohere.embed-multilingual-v3"
    assert [len(t) for t in request["texts"]] == [2048, 5]
    assert request["texts"][0].startswith("Title\nHeading\n\n")
    # A list of vectors comes back only without "embedding_types".
    assert request["input_type"] == "search_document" and "embedding_types" not in request
    assert len(vectors) == 2 and len(vectors[0]) == 1024


def test_cohere_is_called_in_batches_of_96():
    client = FakeBedrock()
    vectors = BedrockEmbedder(client, "cohere.embed-multilingual-v3").embed([f"passage {i}" for i in range(200)])
    assert [len(request["texts"]) for _, request in client.calls] == [96, 96, 8]
    assert len(vectors) == 200


def test_cohere_queries_are_embedded_as_queries():
    client = FakeBedrock()
    BedrockEmbedder(client, "cohere.embed-multilingual-v3").embed_query("What are the visa requirements?")
    [(_, request)] = client.calls
    assert request == {"texts": ["What are the visa requirements?"], "input_type": "search_query", "truncate": "END"}


def test_titan_still_takes_one_text_per_call_unclipped():
    client = FakeBedrock()
    BedrockEmbedder(client, "amazon.titan-embed-text-v2:0").embed(["a" * 3000, "b"])
    assert [request["inputText"] for _, request in client.calls] == ["a" * 3000, "b"]


def test_vectors_of_the_wrong_size_are_refused():
    with pytest.raises(EmbeddingUnavailable, match="wrong size"):
        BedrockEmbedder(FakeBedrock(dimensions=384), "cohere.embed-multilingual-v3").embed(["text"])
