"""OpenAI embeddings (EMBEDDING_PROVIDER=openai): text-embedding-3-small (default) or -large.

Both are asked for EMBEDDING_DIMENSIONS (1024) values, the size of the database column, so
switching between them and the Bedrock models needs no migration. Plain HTTPS, no SDK: one
POST /embeddings per batch, with retries on rate limits and server errors.

OPENAI_BASE_URL points it at another OpenAI-compatible endpoint (Azure OpenAI's /openai/v1,
a proxy); the model must accept the `dimensions` parameter.
"""

import json
import logging
import random
import time
import urllib.error
import urllib.request

from app.constants import EMBEDDING_DIMENSIONS
from app.services.ingestion.embeddings import Embedder
from app.services.ingestion.errors import EmbeddingUnavailable

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIEmbedder(Embedder):
    # Inputs per request (the API takes up to 2048 and 300k tokens; passages are ~400 tokens).
    BATCH = 100
    # A passage over the model's 8192 tokens fails the whole request. Passages are far shorter;
    # this only guards against a pathological one (about 4 characters per token).
    MAX_CHARS = 24_000
    RETRY_STATUSES = {408, 409, 429, 500, 502, 503, 504}

    def __init__(
        self,
        api_key: str | None,
        model_id: str,
        *,
        dimensions: int = EMBEDDING_DIMENSIONS,
        base_url: str | None = None,
        timeout: float = 60,
        max_attempts: int = 5,
        urlopen=urllib.request.urlopen,
        sleep=time.sleep,
    ):
        self.model_id = model_id
        self._api_key = api_key
        self._dimensions = dimensions
        self._url = (base_url or DEFAULT_BASE_URL).rstrip("/") + "/embeddings"
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._urlopen = urlopen
        self._sleep = sleep

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self._api_key:
            # Checked here rather than at startup, like Bedrock's credentials: the API still
            # starts, and the document fails with a message.
            log.error("EMBEDDING_PROVIDER=openai but OPENAI_API_KEY is not set.")
            raise EmbeddingUnavailable("OPENAI_API_KEY is not set.")
        vectors = []
        for start in range(0, len(texts), self.BATCH):
            batch = [text[: self.MAX_CHARS] for text in texts[start:start + self.BATCH]]
            data = self._post({
                "model": self.model_id,
                "input": batch,
                "dimensions": self._dimensions,
                "encoding_format": "float",
            })["data"]
            vectors.extend(item["embedding"] for item in sorted(data, key=lambda item: item["index"]))
        if len(vectors) != len(texts) or any(len(v) != self._dimensions for v in vectors):
            raise EmbeddingUnavailable(
                f"{self.model_id} returned vectors of the wrong size; the database expects {self._dimensions}."
            )
        return vectors

    def _post(self, body: dict) -> dict:
        payload = json.dumps(body).encode()
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        for attempt in range(1, self._max_attempts + 1):
            request = urllib.request.Request(self._url, data=payload, headers=headers, method="POST")
            try:
                with self._urlopen(request, timeout=self._timeout) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as exc:
                detail = _error_message(exc)
                if exc.code not in self.RETRY_STATUSES or attempt == self._max_attempts:
                    log.error("OpenAI embedding call failed (%s): HTTP %s %s", self.model_id, exc.code, detail)
                    raise EmbeddingUnavailable(f"OpenAI answered {exc.code}: {detail}") from exc
                delay = _retry_after(exc) or _backoff(attempt)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
                if attempt == self._max_attempts:
                    log.error("OpenAI embedding call failed (%s): %s", self.model_id, exc)
                    raise EmbeddingUnavailable(str(exc)) from exc
                delay = _backoff(attempt)
            log.warning("OpenAI embedding call failed (attempt %d); retrying in %.1fs", attempt, delay)
            self._sleep(delay)
        raise EmbeddingUnavailable("OpenAI embedding call failed.")  # not reached


def _backoff(attempt: int) -> float:
    return min(30.0, 2 ** (attempt - 1)) + random.uniform(0, 0.5)


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    try:
        return min(60.0, float(exc.headers.get("Retry-After")))
    except (TypeError, ValueError, AttributeError):
        return None


def _error_message(exc: urllib.error.HTTPError) -> str:
    """OpenAI's own explanation ("Incorrect API key provided", "insufficient_quota") when it
    sent one. Never the request, which holds the key."""
    try:
        return json.loads(exc.read())["error"]["message"]
    except Exception:
        return exc.reason or ""
