"""Embedding models for the passages' vectors.

- `BedrockEmbedder`: Amazon Bedrock, Titan Text Embeddings V2 (default) or Cohere Embed v3.
  Credentials come from the standard AWS chain, like S3; the IAM identity needs
  bedrock:InvokeModel on the model.
- `NoEmbedder` (EMBEDDING_PROVIDER=none): passages are stored without vectors, so only keyword
  search can find them. `flask documents reprocess --all` adds vectors later.
"""

import json
import logging
from abc import ABC, abstractmethod

from botocore.exceptions import BotoCoreError, ClientError

from app.constants import EMBEDDING_DIMENSIONS
from app.services.ingestion.errors import EmbeddingUnavailable

log = logging.getLogger(__name__)


class Embedder(ABC):
    #: Stored on the document, so vectors from different models are never mixed unnoticed.
    model_id: str | None = None

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]] | None:
        """One vector per text, or None when this provider stores no vectors.
        Raises EmbeddingUnavailable when the model can't be reached."""

    def embed_query(self, text: str) -> list[float] | None:
        """The vector of a search query, comparable with the passages' vectors (None without
        vectors). Raises EmbeddingUnavailable when the model can't be reached."""
        vectors = self.embed([text])
        return vectors[0] if vectors else None


class NoEmbedder(Embedder):
    model_id = None

    def embed(self, texts: list[str]) -> None:
        return None


class BedrockEmbedder(Embedder):
    """Bedrock's InvokeModel. Titan takes one text per call; Cohere takes up to 96.

    `client` is a boto3 bedrock-runtime client, or a function that makes one. The function is
    only called on the first embedding, so a missing region or credentials never stops the API
    from starting; it fails that document with a message instead.
    """

    COHERE_BATCH = 96

    def __init__(self, client, model_id: str, dimensions: int = EMBEDDING_DIMENSIONS):
        self._client = client
        self.model_id = model_id
        self._dimensions = dimensions
        self._cohere = model_id.startswith("cohere.embed")

    def _get_client(self):
        if callable(self._client) and not hasattr(self._client, "invoke_model"):
            try:
                self._client = self._client()
            except BotoCoreError as exc:
                log.error("Can't create the Bedrock client for embeddings: %s", exc)
                raise EmbeddingUnavailable(str(exc)) from exc
        return self._client

    def embed(self, texts: list[str], input_type: str = "search_document") -> list[list[float]]:
        if self._cohere:
            vectors = []
            for start in range(0, len(texts), self.COHERE_BATCH):
                batch = texts[start:start + self.COHERE_BATCH]
                body = {"texts": batch, "input_type": input_type, "truncate": "END"}
                vectors.extend(self._invoke(body)["embeddings"])
        else:
            vectors = [
                self._invoke({"inputText": text, "dimensions": self._dimensions, "normalize": True})["embedding"]
                for text in texts
            ]
        if any(len(v) != self._dimensions for v in vectors):
            raise EmbeddingUnavailable(
                f"{self.model_id} returned vectors of the wrong size; the database expects {self._dimensions}."
            )
        return vectors

    def embed_query(self, text: str) -> list[float]:
        # Cohere embeds questions differently from the passages they should find; Titan doesn't.
        return self.embed([text], input_type="search_query")[0]

    def _invoke(self, body: dict) -> dict:
        client = self._get_client()
        try:
            response = client.invoke_model(
                modelId=self.model_id,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
            return json.loads(response["body"].read())
        except (BotoCoreError, ClientError, KeyError, ValueError) as exc:
            log.exception("Bedrock embedding call failed (%s)", self.model_id)
            raise EmbeddingUnavailable(str(exc)) from exc


def build_embedder(config) -> Embedder:
    provider = (config.get("EMBEDDING_PROVIDER") or "bedrock").lower()
    if provider == "none":
        log.warning("EMBEDDING_PROVIDER=none: document passages are stored without vectors.")
        return NoEmbedder()
    if provider == "bedrock":
        region = config.get("EMBEDDING_REGION") or None

        def make_client():
            import boto3
            from botocore.config import Config as BotoConfig

            return boto3.client(
                "bedrock-runtime",
                region_name=region,
                # Retries throttling with backoff; fail fast when Bedrock is unreachable.
                config=BotoConfig(retries={"max_attempts": 5, "mode": "adaptive"}, connect_timeout=5, read_timeout=60),
            )

        return BedrockEmbedder(make_client, config["EMBEDDING_MODEL_ID"])
    raise RuntimeError(f"Unknown EMBEDDING_PROVIDER '{provider}'. Use 'bedrock' or 'none'.")
