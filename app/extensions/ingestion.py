"""Wires document ingestion into the app: the embedding model (built once), the services (per
request), and where processing runs.

INGESTION_MODE=thread (default): a background thread in the API process works through waiting
documents after each upload. Simple, and enough for a handful of uploads; a document caught by
a restart is picked up again on the next upload or when the admin list is next polled.

INGESTION_MODE=worker: the API only stores uploads; `flask documents worker` (a separate
process or container) polls and processes them. Use this in production.
"""

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from flask import Flask, current_app

from app.database import db
from app.extensions.storage import get_media_storage
from app.services.document_search_service import DocumentSearchService
from app.services.document_service import DocumentService
from app.services.document_validator import DocumentValidator
from app.services.ingestion import ChunkingOptions, Embedder, HybridChunker, build_embedder
from app.services.ingestion_service import IngestionService
from app.services.knowledge_base import BedrockKnowledgeBase, build_knowledge_base

log = logging.getLogger(__name__)

_EMBEDDER_KEY = "document_embedder"
_KNOWLEDGE_BASE_KEY = "document_knowledge_base"
_RUNNER_KEY = "ingestion_runner"


class ThreadRunner:
    """Processes waiting documents in a background thread of this process, one drain at a time."""

    def __init__(self, app: Flask):
        self._app = app
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingestion")
        self._lock = threading.Lock()
        self._future: Future | None = None
        self._again = False

    def kick(self) -> None:
        with self._lock:
            if self._future is not None and not self._future.done():
                # Already draining: make it look once more before it stops.
                self._again = True
                return
            self._again = False
            self._future = self._executor.submit(self._drain)

    def wait(self, timeout: float | None = None) -> None:
        """Blocks until the current drain is done (for tests and shutdown)."""
        future = self._future
        if future is not None:
            future.result(timeout)

    def _drain(self) -> None:
        while True:
            try:
                with self._app.app_context():
                    get_ingestion_service().run_pending()
            except Exception:
                log.exception("Background document processing failed")
            with self._lock:
                if not self._again:
                    return
                self._again = False


_NO_KNOWLEDGE_BASE = object()


def init_ingestion(app: Flask, embedder: Embedder | None = None, knowledge_base=_NO_KNOWLEDGE_BASE) -> None:
    """Creates the embedding model client and the search index (pgvector, or a Bedrock
    Knowledge Base) once per app, from EMBEDDING_PROVIDER / EMBEDDING_MODEL_ID and
    RETRIEVAL_BACKEND (pass them to override that in tests), and in thread mode the background
    runner."""
    app.extensions[_EMBEDDER_KEY] = embedder or build_embedder(app.config)
    app.extensions[_KNOWLEDGE_BASE_KEY] = (
        build_knowledge_base(app.config) if knowledge_base is _NO_KNOWLEDGE_BASE else knowledge_base
    )
    mode = (app.config.get("INGESTION_MODE") or "thread").lower()
    if mode not in ("thread", "worker"):
        raise RuntimeError(f"Unknown INGESTION_MODE '{mode}'. Use 'thread' or 'worker'.")
    app.extensions[_RUNNER_KEY] = ThreadRunner(app) if mode == "thread" else None


def get_runner() -> ThreadRunner | None:
    return current_app.extensions.get(_RUNNER_KEY)


def kick_ingestion() -> None:
    """Starts background processing (thread mode); a worker finds new documents on its own."""
    runner = get_runner()
    if runner is not None:
        runner.kick()


def get_knowledge_base() -> BedrockKnowledgeBase | None:
    return current_app.extensions.get(_KNOWLEDGE_BASE_KEY)


def get_document_service() -> DocumentService:
    return DocumentService(
        db.session, get_media_storage(), DocumentValidator(), on_uploaded=kick_ingestion, knowledge_base=get_knowledge_base()
    )


def get_document_search_service() -> DocumentSearchService:
    return DocumentSearchService(db.session, current_app.extensions[_EMBEDDER_KEY], get_knowledge_base())


def get_ingestion_service() -> IngestionService:
    config = current_app.config
    return IngestionService(
        db.session,
        get_media_storage(),
        current_app.extensions[_EMBEDDER_KEY],
        HybridChunker(
            ChunkingOptions(
                max_tokens=config["CHUNK_MAX_TOKENS"],
                min_tokens=config["CHUNK_MIN_TOKENS"],
                overlap_tokens=config["CHUNK_OVERLAP_TOKENS"],
            )
        ),
        lease_minutes=config["INGESTION_LEASE_MINUTES"],
        max_attempts=config["INGESTION_MAX_ATTEMPTS"],
        knowledge_base=get_knowledge_base(),
    )
