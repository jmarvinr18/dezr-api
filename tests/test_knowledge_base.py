"""RETRIEVAL_BACKEND=bedrock_kb, and switching between models and backends."""

import uuid

import pytest
from botocore.exceptions import ClientError

from app.database import db
from app.extensions.ingestion import init_ingestion
from app.model import Document
from app.services.ingestion_service import EMBEDDING_FAILED, KNOWLEDGE_BASE_DOWN
from app.services.knowledge_base import BedrockKnowledgeBase, build_knowledge_base
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, login, make_app
from tests.document_fakes import FakeEmbedder
from tests.test_documents_api import GUIDE_MD, URL, process, upload

SEARCH_KEY = "test-search-key-0123456789abcdef"


def client_error(operation):
    return ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, operation)


class FakeAws:
    """The S3, bedrock-agent and bedrock-agent-runtime calls the knowledge base makes."""

    def __init__(self, statuses=("IN_PROGRESS", "INDEXED"), down=False):
        self.statuses = list(statuses)
        self.down = down
        self.objects = {}
        self.ingested = []
        self.deleted = []
        self.results = []
        self.retrieve_calls = []

    def __call__(self, service):
        return self

    # s3
    def put_object(self, Bucket, Key, Body, ContentType):
        if self.down:
            raise client_error("PutObject")
        self.objects[(Bucket, Key)] = (Body, ContentType)

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)

    # bedrock-agent
    def ingest_knowledge_base_documents(self, knowledgeBaseId, dataSourceId, documents):
        self.ingested.append((knowledgeBaseId, dataSourceId, documents))

    def get_knowledge_base_documents(self, knowledgeBaseId, dataSourceId, documentIdentifiers):
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return {"documentDetails": [{"status": status, "statusReason": "Unsupported file", "identifier": documentIdentifiers[0]}]}

    def delete_knowledge_base_documents(self, knowledgeBaseId, dataSourceId, documentIdentifiers):
        self.deleted.append(documentIdentifiers[0]["s3"]["uri"])

    # bedrock-agent-runtime
    def retrieve(self, knowledgeBaseId, retrievalQuery, retrievalConfiguration):
        self.retrieve_calls.append((retrievalQuery, retrievalConfiguration))
        if self.down:
            raise client_error("Retrieve")
        return {"retrievalResults": self.results}


def knowledge_base(aws, **kwargs):
    return BedrockKnowledgeBase(
        "KB12345678", "DS12345678", "s3://kb-bucket/dezr", clients=aws, poll_seconds=0, sleep=lambda _: None, **kwargs
    )


@pytest.fixture
def kb_aws():
    return FakeAws()


@pytest.fixture
def kb_app(tmp_path, aws, kb_aws):
    return make_app(
        tmp_path,
        knowledge_base=knowledge_base(kb_aws),
        RETRIEVAL_BACKEND="bedrock_kb",
        DOCUMENT_SEARCH_API_KEY=SEARCH_KEY,
    )


@pytest.fixture
def admin_headers_for():
    """(client, admin headers) for an app this test made."""

    def sign_in(app):
        result = app.test_cli_runner().invoke(args=["admin", "create", ADMIN_EMAIL, "--password", ADMIN_PASSWORD])
        assert result.exit_code == 0, result.output
        client = app.test_client()
        return client, {"Authorization": f"Bearer {login(client).get_json()['token']}"}

    return sign_in


def switch(app, embedder=None, knowledge_base=None):
    """As after changing the settings and restarting."""
    init_ingestion(app, embedder or FakeEmbedder(), knowledge_base)


def stored(app, document_id):
    with app.app_context():
        return db.session.get(Document, uuid.UUID(document_id))


def test_documents_are_copied_to_the_data_source_and_indexed(kb_app, kb_aws, admin_headers_for):
    client, headers = admin_headers_for(kb_app)
    document = upload(client, headers, "guide.md", GUIDE_MD, title="Doing Business").get_json()
    process(kb_app)

    row = stored(kb_app, document["id"])
    assert row.status == "ready" and row.embedding_model == "bedrock-kb:KB12345678"
    # Passages are still kept here, without vectors, for the preview and the keyword fallback.
    assert row.chunk_count > 0
    assert kb_aws.objects[("kb-bucket", f"dezr/{document['id']}.md")] == (GUIDE_MD, "text/markdown")
    [(kb_id, ds_id, [sent])] = kb_aws.ingested
    assert (kb_id, ds_id) == ("KB12345678", "DS12345678")
    assert sent["content"]["s3"]["s3Location"]["uri"] == f"s3://kb-bucket/dezr/{document['id']}.md"
    attributes = {a["key"]: a["value"]["stringValue"] for a in sent["metadata"]["inlineAttributes"]}
    assert attributes == {"documentId": document["id"], "title": "Doing Business"}

    assert client.delete(f"{URL}/{document['id']}", headers=headers).status_code == 204
    assert kb_aws.deleted == [f"s3://kb-bucket/dezr/{document['id']}.md"]
    assert kb_aws.objects == {}


def test_a_file_the_knowledge_base_rejects_fails_with_its_reason(tmp_path, aws, admin_headers_for):
    app = make_app(tmp_path, knowledge_base=knowledge_base(FakeAws(statuses=["FAILED"])), RETRIEVAL_BACKEND="bedrock_kb")
    client, headers = admin_headers_for(app)
    document = upload(client, headers, "guide.md", GUIDE_MD).get_json()
    process(app)
    row = stored(app, document["id"])
    assert row.status == "failed" and row.error == "The knowledge base couldn't index the document: Unsupported file"


def test_an_unreachable_knowledge_base_fails_the_document(tmp_path, aws, admin_headers_for):
    app = make_app(tmp_path, knowledge_base=knowledge_base(FakeAws(down=True)), RETRIEVAL_BACKEND="bedrock_kb")
    client, headers = admin_headers_for(app)
    document = upload(client, headers, "guide.md", GUIDE_MD).get_json()
    process(app)
    assert stored(app, document["id"]).error == KNOWLEDGE_BASE_DOWN


def test_a_slow_index_still_leaves_the_document_ready(tmp_path, aws, admin_headers_for):
    slow = FakeAws(statuses=["IN_PROGRESS"])
    app = make_app(tmp_path, knowledge_base=knowledge_base(slow, wait_seconds=0), RETRIEVAL_BACKEND="bedrock_kb")
    client, headers = admin_headers_for(app)
    document = upload(client, headers, "guide.md", GUIDE_MD).get_json()
    process(app)
    assert stored(app, document["id"]).status == "ready"


def test_search_answers_from_the_knowledge_base_for_existing_documents(kb_app, kb_aws, admin_headers_for):
    client, headers = admin_headers_for(kb_app)
    document = upload(client, headers, "guide.md", GUIDE_MD, title="Doing Business").get_json()
    process(kb_app)
    gone = str(uuid.uuid4())
    kb_aws.results = [
        {"content": {"text": "VAT is 12%."}, "score": 0.81,
         "metadata": {"documentId": document["id"], "x-amz-bedrock-kb-document-page-number": 3.0}},
        # Indexed by a data source sync, without metadata: the file name is the id.
        {"content": {"text": "Corporate income tax is 25%."}, "score": 0.7,
         "location": {"s3Location": {"uri": f"s3://kb-bucket/dezr/{document['id']}.md"}}, "metadata": {}},
        # Deleted here, still in the index.
        {"content": {"text": "Stale."}, "score": 0.9, "metadata": {"documentId": gone}},
    ]

    response = client.post(
        "/api/v1/documents/search", json={"query": "VAT rate", "limit": 3}, headers={"Authorization": f"Bearer {SEARCH_KEY}"}
    )

    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["mode"] == "knowledge_base"
    assert [item["content"] for item in body["items"]] == ["VAT is 12%.", "Corporate income tax is 25%."]
    first = body["items"][0]
    assert first["documentId"] == document["id"] and first["title"] == "Doing Business"
    assert first["pageStart"] == 3 and first["chunkId"] is None and first["score"] == 0.81
    [(query, configuration)] = kb_aws.retrieve_calls
    assert query == {"text": "VAT rate"}
    assert configuration == {"vectorSearchConfiguration": {"numberOfResults": 3}}


def test_the_search_type_can_be_chosen():
    aws = FakeAws()
    knowledge_base(aws, search_type="hybrid").retrieve("VAT", 5)
    assert aws.retrieve_calls[0][1]["vectorSearchConfiguration"]["overrideSearchType"] == "HYBRID"


def test_settings_choose_the_backend():
    assert build_knowledge_base({"RETRIEVAL_BACKEND": "pgvector"}) is None
    assert build_knowledge_base({}) is None
    kb = build_knowledge_base({
        "RETRIEVAL_BACKEND": "bedrock_kb",
        "KNOWLEDGE_BASE_ID": "KB1",
        "KNOWLEDGE_BASE_DATA_SOURCE_ID": "DS1",
        "KNOWLEDGE_BASE_S3_URI": "s3://bucket/kb/",
    })
    assert kb.index_id == "bedrock-kb:KB1"
    with pytest.raises(RuntimeError, match="KNOWLEDGE_BASE_DATA_SOURCE_ID, KNOWLEDGE_BASE_S3_URI"):
        build_knowledge_base({"RETRIEVAL_BACKEND": "bedrock_kb", "KNOWLEDGE_BASE_ID": "KB1"})
    with pytest.raises(RuntimeError, match="pgvector"):
        build_knowledge_base({"RETRIEVAL_BACKEND": "opensearch"})


# ---------- switching models and backends ----------


class OtherEmbedder(FakeEmbedder):
    model_id = "other-embed-v2"


def test_switching_the_model_requeues_documents_from_the_old_one(tmp_path, aws, admin_headers_for):
    old = make_app(tmp_path, embedder=FakeEmbedder())
    client, headers = admin_headers_for(old)
    ready = upload(client, headers, "guide.md", GUIDE_MD).get_json()
    process(old)
    # A document that failed because the old model was unreachable is tried again too.
    failed = upload(client, headers, "other.md", GUIDE_MD).get_json()
    with old.app_context():
        row = db.session.get(Document, uuid.UUID(failed["id"]))
        row.status, row.error = "failed", EMBEDDING_FAILED
        db.session.commit()

    new_embedder = OtherEmbedder()
    switch(old, new_embedder)
    new = old
    runner = new.test_cli_runner()
    assert "other-embed-v2" in runner.invoke(args=["documents", "index"]).output
    assert "Queued 2 document(s)." in runner.invoke(args=["documents", "reprocess", "--stale"]).output
    process(new)
    assert stored(new, ready["id"]).embedding_model == "other-embed-v2"
    assert stored(new, failed["id"]).status == "ready"
    assert new_embedder.calls
    # Nothing left to bring over.
    assert "Queued 0 document(s)." in runner.invoke(args=["documents", "reprocess", "--stale"]).output


def test_switching_to_the_knowledge_base_requeues_too(tmp_path, aws, kb_aws, admin_headers_for):
    old = make_app(tmp_path)
    client, headers = admin_headers_for(old)
    document = upload(client, headers, "guide.md", GUIDE_MD).get_json()
    process(old)

    switch(old, knowledge_base=knowledge_base(kb_aws))
    new = old
    assert "Queued 1 document(s)." in new.test_cli_runner().invoke(args=["documents", "reprocess", "--stale"]).output
    process(new)
    assert stored(new, document["id"]).embedding_model == "bedrock-kb:KB12345678"
    assert len(kb_aws.ingested) == 1


def test_keyword_only_mode_requeues_nothing(tmp_path, aws, admin_headers_for):
    from app.services.ingestion import NoEmbedder

    old = make_app(tmp_path)
    client, headers = admin_headers_for(old)
    upload(client, headers, "guide.md", GUIDE_MD)
    process(old)
    switch(old, NoEmbedder())
    new = old
    assert "Queued 0 document(s)." in new.test_cli_runner().invoke(args=["documents", "reprocess", "--stale"]).output
