"""A Bedrock Knowledge Base as the documents' search index (RETRIEVAL_BACKEND=bedrock_kb).

The knowledge base does its own chunking and embedding, with whatever model it was created
with. dezr-api still parses and chunks each document (the admin's passage preview, and the
keyword fallback), and then:
- copies the file to the knowledge base's S3 data source (KNOWLEDGE_BASE_S3_URI), named by
  the document's id, and indexes just that file (IngestKnowledgeBaseDocuments), with the
  document's id and title as metadata; it waits for the index to report the outcome, up to
  KNOWLEDGE_BASE_WAIT_SECONDS;
- removes it from the index and the data source when the document is deleted;
- answers searches with the knowledge base's Retrieve API, citing the documents it knows.

The knowledge base and its S3 data source are made outside this app (console or IaC). The AWS
identity needs s3:PutObject/DeleteObject on the data source prefix and
bedrock:IngestKnowledgeBaseDocuments, GetKnowledgeBaseDocuments, DeleteKnowledgeBaseDocuments
and Retrieve on the knowledge base.
"""

import logging
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

from botocore.exceptions import BotoCoreError, ClientError

log = logging.getLogger(__name__)

CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "md": "text/markdown",
    "txt": "text/plain",
}
INDEXED = {"INDEXED", "PARTIALLY_INDEXED", "METADATA_PARTIALLY_INDEXED"}
FAILED = {"FAILED", "IGNORED", "METADATA_UPDATE_FAILED"}
PAGE_KEY = "x-amz-bedrock-kb-document-page-number"


class KnowledgeBaseUnavailable(Exception):
    """The knowledge base (or its S3 data source) couldn't be reached or refused the request."""


class KnowledgeBaseRejected(Exception):
    """The knowledge base read the file but couldn't index it; `reason` is its explanation."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class RetrievedPassage:
    document_id: uuid.UUID | None
    content: str
    page: int | None
    score: float | None


class BedrockKnowledgeBase:
    def __init__(
        self,
        knowledge_base_id: str,
        data_source_id: str,
        s3_uri: str,
        *,
        clients,
        wait_seconds: float = 300,
        poll_seconds: float = 3,
        search_type: str | None = None,
        sleep=time.sleep,
    ):
        """`clients` makes the boto3 clients: a function of the service name ("s3",
        "bedrock-agent", "bedrock-agent-runtime"). Called on first use only, so a missing
        region or credentials never stops the API from starting."""
        parsed = urlparse(s3_uri)
        if parsed.scheme != "s3" or not parsed.netloc:
            raise RuntimeError(f"KNOWLEDGE_BASE_S3_URI must look like s3://bucket/prefix/, not '{s3_uri}'.")
        self.knowledge_base_id = knowledge_base_id
        self.data_source_id = data_source_id
        self._bucket = parsed.netloc
        prefix = parsed.path.lstrip("/")
        self._prefix = prefix if not prefix or prefix.endswith("/") else prefix + "/"
        self._make_client = clients
        self._clients = {}
        self._wait_seconds = wait_seconds
        self._poll_seconds = poll_seconds
        self._search_type = (search_type or "").upper() or None
        self._sleep = sleep

    @property
    def index_id(self) -> str:
        """Stored as the document's embedding_model: where its passages are searchable."""
        return f"bedrock-kb:{self.knowledge_base_id}"

    def _client(self, service: str):
        if service not in self._clients:
            try:
                self._clients[service] = self._make_client(service)
            except BotoCoreError as exc:
                raise KnowledgeBaseUnavailable(str(exc)) from exc
        return self._clients[service]

    def _key(self, document_id: uuid.UUID, kind: str) -> str:
        return f"{self._prefix}{document_id}.{kind}"

    def _uri(self, document_id: uuid.UUID, kind: str) -> str:
        return f"s3://{self._bucket}/{self._key(document_id, kind)}"

    # ---------- Indexing ----------

    def put(self, document_id: uuid.UUID, kind: str, title: str, data: bytes) -> str:
        """Copies the file to the data source and indexes it. Returns the index's status
        (INDEXED, or STARTING/IN_PROGRESS if it was still working when the wait ran out).
        Raises KnowledgeBaseRejected when the index failed the file."""
        uri = self._uri(document_id, kind)
        try:
            self._client("s3").put_object(
                Bucket=self._bucket,
                Key=self._key(document_id, kind),
                Body=data,
                ContentType=CONTENT_TYPES.get(kind, "application/octet-stream"),
            )
            self._client("bedrock-agent").ingest_knowledge_base_documents(
                knowledgeBaseId=self.knowledge_base_id,
                dataSourceId=self.data_source_id,
                documents=[{
                    "content": {"dataSourceType": "S3", "s3": {"s3Location": {"uri": uri}}},
                    "metadata": {
                        "type": "IN_LINE_ATTRIBUTE",
                        "inlineAttributes": [
                            {"key": "documentId", "value": {"type": "STRING", "stringValue": str(document_id)}},
                            {"key": "title", "value": {"type": "STRING", "stringValue": title[:1000]}},
                        ],
                    },
                }],
            )
        except (BotoCoreError, ClientError) as exc:
            log.exception("Sending document %s to knowledge base %s failed", document_id, self.knowledge_base_id)
            raise KnowledgeBaseUnavailable(str(exc)) from exc
        return self._wait_until_indexed(document_id, uri)

    def _wait_until_indexed(self, document_id: uuid.UUID, uri: str) -> str:
        deadline = time.monotonic() + self._wait_seconds
        status = "STARTING"
        while True:
            try:
                details = self._client("bedrock-agent").get_knowledge_base_documents(
                    knowledgeBaseId=self.knowledge_base_id,
                    dataSourceId=self.data_source_id,
                    documentIdentifiers=[{"dataSourceType": "S3", "s3": {"uri": uri}}],
                )["documentDetails"]
            except (BotoCoreError, ClientError) as exc:
                log.warning("Checking document %s in the knowledge base failed: %s", document_id, exc)
                details = []
            if details:
                status = details[0]["status"]
                if status in INDEXED:
                    return status
                if status in FAILED:
                    raise KnowledgeBaseRejected(details[0].get("statusReason") or status)
            if time.monotonic() >= deadline:
                log.warning(
                    "Knowledge base still indexing document %s (%s) after %ss; it becomes searchable when done.",
                    document_id, status, self._wait_seconds,
                )
                return status
            self._sleep(self._poll_seconds)

    def remove(self, document_id: uuid.UUID, kind: str) -> None:
        """Takes the document out of the index and the data source. Failures are logged, not
        raised: search ignores passages of documents that no longer exist."""
        uri = self._uri(document_id, kind)
        try:
            self._client("bedrock-agent").delete_knowledge_base_documents(
                knowledgeBaseId=self.knowledge_base_id,
                dataSourceId=self.data_source_id,
                documentIdentifiers=[{"dataSourceType": "S3", "s3": {"uri": uri}}],
            )
        except (BotoCoreError, ClientError, KnowledgeBaseUnavailable):
            log.exception("Removing document %s from knowledge base %s failed", document_id, self.knowledge_base_id)
        try:
            self._client("s3").delete_object(Bucket=self._bucket, Key=self._key(document_id, kind))
        except (BotoCoreError, ClientError, KnowledgeBaseUnavailable):
            log.exception("Deleting %s from the knowledge base's data source failed", uri)

    # ---------- Search ----------

    def retrieve(self, query: str, limit: int) -> list[RetrievedPassage]:
        vector_search = {"numberOfResults": limit}
        if self._search_type:
            # HYBRID needs a vector store that supports it (OpenSearch Serverless, Aurora, ...).
            vector_search["overrideSearchType"] = self._search_type
        try:
            results = self._client("bedrock-agent-runtime").retrieve(
                knowledgeBaseId=self.knowledge_base_id,
                retrievalQuery={"text": query},
                retrievalConfiguration={"vectorSearchConfiguration": vector_search},
            )["retrievalResults"]
        except (BotoCoreError, ClientError) as exc:
            log.warning("Knowledge base %s search failed: %s", self.knowledge_base_id, exc)
            raise KnowledgeBaseUnavailable(str(exc)) from exc
        return [self._passage(result) for result in results]

    def _passage(self, result: dict) -> RetrievedPassage:
        metadata = result.get("metadata") or {}
        page = metadata.get(PAGE_KEY)
        return RetrievedPassage(
            document_id=_as_uuid(metadata.get("documentId")) or self._id_from_uri(result),
            content=(result.get("content") or {}).get("text") or "",
            page=int(page) if isinstance(page, (int, float)) else None,
            score=result.get("score"),
        )

    def _id_from_uri(self, result: dict) -> uuid.UUID | None:
        """For a file indexed without metadata (e.g. by a data source sync): its name is the id."""
        uri = (((result.get("location") or {}).get("s3Location") or {}).get("uri")) or ""
        return _as_uuid(uri.rsplit("/", 1)[-1].split(".", 1)[0])


def _as_uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def build_knowledge_base(config) -> BedrockKnowledgeBase | None:
    """The knowledge base when RETRIEVAL_BACKEND=bedrock_kb, else None (pgvector)."""
    backend = (config.get("RETRIEVAL_BACKEND") or "pgvector").strip().lower()
    if backend == "pgvector":
        return None
    if backend != "bedrock_kb":
        raise RuntimeError(f"Unknown RETRIEVAL_BACKEND '{backend}'. Use 'pgvector' or 'bedrock_kb'.")
    missing = [
        name for name in ("KNOWLEDGE_BASE_ID", "KNOWLEDGE_BASE_DATA_SOURCE_ID", "KNOWLEDGE_BASE_S3_URI")
        if not config.get(name)
    ]
    if missing:
        raise RuntimeError(f"RETRIEVAL_BACKEND=bedrock_kb needs {', '.join(missing)}.")
    region = config.get("KNOWLEDGE_BASE_REGION") or None

    def make_client(service: str):
        import boto3
        from botocore.config import Config as BotoConfig

        return boto3.client(
            service,
            region_name=region,
            config=BotoConfig(retries={"max_attempts": 5, "mode": "adaptive"}, connect_timeout=5, read_timeout=60),
        )

    return BedrockKnowledgeBase(
        config["KNOWLEDGE_BASE_ID"],
        config["KNOWLEDGE_BASE_DATA_SOURCE_ID"],
        config["KNOWLEDGE_BASE_S3_URI"],
        clients=make_client,
        wait_seconds=float(config.get("KNOWLEDGE_BASE_WAIT_SECONDS") or 300),
        search_type=config.get("KNOWLEDGE_BASE_SEARCH_TYPE"),
    )
