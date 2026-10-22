# dezr API

Flask backend for `dezr-ui` (the Nuxt site for deszr.com). It holds all of the site's content:
the site settings (firm details, menus, slideshow, download banners, footer, assistant wording),
the content pages, uploaded media, contact-form enquiries, the documents the AI assistant can
search (stored, chunked and embedded for RAG), and the admin accounts that change them.

Structured like `vendor-portal-api`: thin flask-smorest blueprints, marshmallow schemas
(camelCase JSON), services that hold the rules and raise domain errors, SQLAlchemy models,
Alembic migrations, and S3 (or local disk) behind a storage interface.

## Structure

```
app/
  __init__.py        create_app(): config, DB, migrations, storage, auth, blueprints, CORS
  config.py          settings (loads app/.env)
  constants.py       sections, slide scenes, banner styles, path/link rules (mirrors dezr-ui/shared/site/schema.ts)
  cli.py             `flask seed-content`, `flask admin create|ensure-default|set-password|deactivate|activate|list`
  database/          SQLAlchemy instance
  extensions/        storage.py: builds the media storage once; auth.py: the token signing key;
                     ingestion.py: the embedding model, and where document processing runs;
                     ai.py: the client for the dezr-ai agent (AgentCore, or HTTP locally)
  model/             admin_user (+ login failures), site_setting, page, media_file, contact_message,
                     document (+ document_chunks with pgvector embeddings)
  schema/            marshmallow request/response schemas (camelCase JSON)
  seed/              the shipped content: site_defaults.py and pages/*.md (copied from dezr-ui/content)
  services/          business logic (no HTTP code; raises errors from services/errors.py)
    site_settings_service.py  defaults + saved sections, save / reset
    page_service.py           public lookup, admin edits, reset to shipped, seeding
    search_service.py         section search (markdown_text.py turns MDC Markdown into text)
    media_service.py          upload / read / delete images and PDFs
    upload_validator.py       file rules: type by magic bytes, size, URL-safe names
    contact_service.py        store enquiries, per-visitor rate limit
    auth_service.py           sign-in, signed tokens, lockout, accounts
    current_admin.py          the admin making the request (Bearer token)
    document_service.py       documents for the assistant: upload, list, delete (cancels processing)
    document_validator.py     PDF / .docx / .txt / .md by content, 25 MB, zip-bomb guard
    ingestion_service.py      the processing queue: claim (lease), parse, chunk, embed, store
    ingestion/                parsers/ (pdf, docx, markdown, plain_text) -> blocks; chunker.py (hybrid
                              chunking); embeddings.py (OpenAI, Bedrock Cohere / Titan, or none); text.py
    ai/                       the assistant's transcript and limits (chat_service.py) and the client
                              for the dezr-ai agent (agent_client.py); no prompts, models or tools
    storage/                  MediaStorage interface + S3 and local implementations
  routes/            flask-smorest blueprints (thin: parse request, call a service)
migrations/          Alembic (Flask-Migrate)
tests/               pytest; S3 is mocked with moto
docs/api-integration.md   endpoint reference, and how dezr-ui switches over to this API
docs/database-schema.md   ER diagram and table notes
```

## What the UI needs, and where it comes from

| dezr-ui feature | Endpoint |
| --- | --- |
| Header, menu, slideshow, mission box, download banners, footer, contact details, assistant wording (`useAppConfig()` via `plugins/cms-settings.ts`) | `GET /api/v1/site-settings` |
| Any page (`pages/[...slug].vue`) | `GET /api/v1/pages/by-path?path=/contact` |
| Site map (`::site-map`) | `GET /api/v1/pages` |
| Search page | `GET /api/v1/search?q=` |
| Uploaded images and PDFs (`/media/<name>`) | `GET /api/v1/media/{name}` |
| Contact form | `POST /api/v1/contact-messages` |
| Admin sign-in / session / sign-out | `POST /api/v1/admin/auth/login`, `GET .../me`, `POST .../logout` |
| Admin > Site settings | `GET, PUT, PATCH, DELETE /api/v1/admin/site-settings`, `DELETE .../{section}` |
| Admin > Pages | `GET, POST /api/v1/admin/pages`, `GET, PATCH, DELETE .../{id}`, `POST .../{id}/reset` |
| Admin > Media | `GET, POST /api/v1/admin/media`, `DELETE .../{name}` |
| Admin > Messages (new) | `GET /api/v1/admin/contact-messages`, `GET, DELETE .../{id}` |
| Admin > Documents (files the assistant can search) | `GET, POST /api/v1/admin/documents`, `POST .../uploads`, `POST .../uploads/{uploadId}/complete`, `GET, DELETE .../{id}`, `GET .../{id}/chunks` |
| Admin form options | `GET /api/v1/reference-data` |
| AI assistant chat (the bubble and `/assistant`) | `POST /api/v1/ai/messages`, `POST /api/v1/ai/messages/stream`, relayed to the dezr-ai agent |

See [docs/api-integration.md](docs/api-integration.md) for every endpoint and a file-by-file plan
for switching dezr-ui over.

## Content model

- **Site settings** are eight sections (`site`, `contact`, `nav`, `slides`, `mission`, `banners`,
  `footer`, `assistant`). A section shows its shipped default (`app/seed/site_defaults.py`) until
  an admin saves it; a saved section replaces the default whole, and resetting a section brings
  the default back.
- **Pages** live in the database. `flask seed-content` loads the shipped ones from
  `app/seed/pages/*.md` (same front matter as dezr-ui's `content/`). Admins can edit, unpublish,
  delete or create pages, and reset a shipped page to its original. Re-running `seed-content`
  creates missing pages and updates pages nobody edited, but never overwrites an admin's edit
  (unless `--force`). The home page (`/`) can't be deleted, moved or unpublished.
- **Page bodies are Markdown with the site's MDC components** (`::person-profile`,
  `::library-grid`, `::contact-section`, `::site-map`, `:japan-desk-email`). The API stores and
  returns them as written; **rendering and sanitising stay in dezr-ui** (`renderMarkdown()` in
  `server/utils/cms.ts`), which strips scripts, event handlers and unsafe links.
- **Media**: images (PNG, JPG, GIF, WebP) and PDFs up to 10 MB. The type is recognised from the
  file's first bytes, never its name; SVG and HTML are refused because they can carry scripts.
  Files are published under a URL-safe name (`Partner Photo.JPG` → `partner-photo.jpg`) and served
  with `nosniff` and a sandboxing Content-Security-Policy.
- **Contact enquiries** are stored for the firm to read in the admin area. Each visitor may send
  `CONTACT_RATE_LIMIT_PER_HOUR` (5) an hour; only a keyed hash of their IP is kept. Email delivery
  is not wired up yet (see `services/contact_service.py`).

## Documents for the AI assistant (RAG ingestion)

Admins upload the files the assistant should be able to search (Admin > Documents in dezr-ui,
contract in `dezr-ui/docs/document-ingestion-api.md`). `POST /api/v1/admin/documents` stores the
file (S3 under `documents/`, or local disk) and answers `202` with `status: "processing"`; the
document becomes `ready` (with `chunkCount`) or `failed` (with an `error` an admin can act on).

**Direct uploads** (S3 only): Amplify Hosting, where dezr-ui runs, refuses request bodies over
about 4.5 MB, so the page sends larger files straight to S3. `POST .../uploads` with the size
returns a presigned form (15 minutes, 1 byte to 25 MB, enforced by S3); the browser posts the
file to it, under `incoming/`; `POST .../uploads/{uploadId}/complete` runs the same checks as
a normal upload, moves the file to `documents/` and queues it. Nothing is recorded before that,
and S3 deletes an upload nobody confirmed after a day (`terraform/storage.tf`). With
`STORAGE_BACKEND=local`, `POST .../uploads` answers `501` and the page uploads the usual way.

**Files:** PDF, Word (.docx), plain text and Markdown, up to 25 MB, recognised by their content
(a renamed .exe is refused with 415; an old .doc is told to save as .docx). Scanned PDFs without a
text layer fail with "The PDF has no readable text" (no OCR here).

**Pipeline** (`services/ingestion_service.py`): read the file → parse it into blocks (headings,
paragraphs, lists, tables, code) → hybrid chunking → embed each passage with its context → store
passages and vectors in `document_chunks` (pgvector).

- **Parsing.** Word and Markdown say what their structure is (heading styles / `#`, list numbering,
  tables). PDFs are read from the layout: larger fonts and bold standalone lines are headings,
  ruled tables are kept whole, words hyphenated across lines are rejoined, running headers,
  footers and page numbers are dropped, and every block keeps its page number. Plain text infers
  headings (`1.2 Fees`, `ARTICLE IV`, ALL CAPS, underlined), lists and tab/pipe tables.
- **Hybrid chunking** (`services/ingestion/chunker.py`): structure first, then size.
  1. Blocks are grouped into sections by heading path; a passage never spans two sections and a
     block is never cut while it fits.
  2. A block over `CHUNK_MAX_TOKENS` is cut at its natural boundary: tables between rows with the
     header repeated, lists between items, code between lines, prose between sentences in windows
     that overlap by `CHUNK_OVERLAP_TOKENS`. A section's pieces are then packed up to the limit.
  3. A passage under `CHUNK_MIN_TOKENS` is merged into a sibling or parent/child neighbour if the
     result fits; the other headings are written into its text.

  Each passage keeps its heading path, page range, block types and token count. It is embedded
  as `title + heading path + text`, so short passages still carry their context.
- **Embeddings** (`EMBEDDING_PROVIDER`, `EMBEDDING_MODEL_ID`), every model at 1024 dimensions:
  - `openai` (default): OpenAI's API with `OPENAI_API_KEY`, `text-embedding-3-small` by default
    (or `text-embedding-3-large`). `OPENAI_BASE_URL` points it at another OpenAI-compatible
    endpoint.
  - `bedrock`: Amazon Bedrock with the same AWS credentials as S3 plus `bedrock:InvokeModel`.
    Cohere Embed Multilingual v3 by default (in-Region in ap-southeast-1); Titan Text Embeddings
    V2 works where Bedrock offers it, which isn't Singapore. Cohere is sold through AWS
    Marketplace: call it once as an admin first (`terraform/README.md`, "Document embeddings").
    Each text is clipped to Cohere's 2,048-character limit; the context comes first, and the
    stored passage stays whole.
  - `none`: passages without vectors (keyword search only).
- **Switching models:** change the settings and restart. Each document records what it was
  indexed with (`documents.embedding_model`), and search only compares vectors from the current
  model. The worker re-indexes documents from another model when it starts (`REINDEX_ON_START`), as
  does `flask documents reprocess --stale`; until then they're found by keywords. Documents that
  failed because the embedding service was down are retried the same way.
- **Or a Bedrock Knowledge Base** (`RETRIEVAL_BACKEND=bedrock_kb`): the knowledge base chunks
  and embeds with its own model. The API copies each upload to the knowledge base's S3 data source
  (`KNOWLEDGE_BASE_S3_URI`, named `<document id>.<ext>`), indexes that file directly
  (IngestKnowledgeBaseDocuments, with the document's id and title as metadata) and waits for the
  outcome; deleting removes it again. Search answers from its Retrieve API, keeping only documents
  that still exist here, and falls back to keyword search when it's unreachable. Passages are
  still chunked here for the admin's preview. The knowledge base itself is made outside the app.
- **Retrieval-ready:** an HNSW index on the vectors (cosine) and a GIN full-text index on the text
  (`simple` config, since the site is in four languages), for hybrid search.
- **Search for the assistant:** `POST /api/v1/documents/search` (key-protected,
  `DOCUMENT_SEARCH_API_KEY`) merges the vector and keyword rankings with reciprocal rank fusion,
  or asks the knowledge base.
  dezr-ai's AgentCore runtime retrieves through it (see docs/api-integration.md).

**Where processing runs** (`INGESTION_MODE`):

- `thread` (default, for `flask run`): a background thread in the API process, started by each upload.
- `worker` (Docker / production): the API only stores; `flask documents worker` processes. The
  compose file runs one `worker` container; run more to process in parallel.

The documents table is the queue. A processor claims a document with a lease
(`INGESTION_LEASE_MINUTES`); if it dies, another picks the document up after the lease, and after
`INGESTION_MAX_ATTEMPTS` claims it is marked failed. Deleting a document removes the file, its
passages and vectors, and cancels processing that is still running.

```bash
uv run flask documents list
uv run flask documents process-pending        # once, e.g. from cron
uv run flask documents worker                 # until stopped
uv run flask documents index                  # what documents are indexed with, and what's next
uv run flask documents reprocess --stale      # after changing the embedding model or backend
uv run flask documents reprocess --all        # after changing the chunking
uv run flask documents reprocess --failed
```

| Setting | Default | |
| --- | --- | --- |
| `INGESTION_MODE` | `thread` | `worker` when `flask documents worker` runs separately |
| `CHUNK_MAX_TOKENS` / `CHUNK_MIN_TOKENS` / `CHUNK_OVERLAP_TOKENS` | `400` / `60` / `50` | Approximate tokens (no tokenizer download) |
| `EMBEDDING_PROVIDER` | `openai` | `openai`, `bedrock` or `none` |
| `EMBEDDING_MODEL_ID` | the provider's | `text-embedding-3-small` / `-large`; `cohere.embed-multilingual-v3` / `amazon.titan-embed-text-v2:0` (not in ap-southeast-1) |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | / `https://api.openai.com/v1` | |
| `EMBEDDING_REGION` | `AWS_DEFAULT_REGION` | Bedrock: a region that offers the model |
| `RETRIEVAL_BACKEND` | `pgvector` | or `bedrock_kb` |
| `KNOWLEDGE_BASE_ID` / `KNOWLEDGE_BASE_DATA_SOURCE_ID` / `KNOWLEDGE_BASE_S3_URI` | | `bedrock_kb`: the knowledge base, its S3 data source and that source's `s3://bucket/prefix/` |
| `KNOWLEDGE_BASE_REGION` / `KNOWLEDGE_BASE_SEARCH_TYPE` | `AWS_DEFAULT_REGION` / its default | `HYBRID` or `SEMANTIC` where the vector store supports it |
| `KNOWLEDGE_BASE_WAIT_SECONDS` | `300` | How long processing waits for the index before marking the document ready anyway |
| `REINDEX_ON_START` | `1` | The worker re-indexes documents from another model or backend when it starts |
| `INGESTION_LEASE_MINUTES` / `INGESTION_MAX_ATTEMPTS` | `15` / `3` | |
| `INGESTION_POLL_SECONDS` | `5` | Worker's wait when nothing is queued |

**Database:** migration `0002_documents` needs PostgreSQL with the **pgvector** extension (the
`pgvector/pgvector:pg17` image, or RDS / Aurora PostgreSQL 15+) and enables it; it stops with a
clear message on a server without it.

## AI assistant (the dezr-ai agent on Bedrock AgentCore)

The agent is **not** in this repository: it is **dezr-ai** (LangGraph, RAG over the documents
above), deployed to Amazon Bedrock AgentCore Runtime. This API is its front door: it takes a
visitor's message, relays it to the agent, and stores the conversation. dezr-ui's chat (bubble
and `/assistant` page) uses the same contract as vendor-portal-api's, so it works unchanged.

```
dezr-ui ──POST /api/v1/ai/messages/stream──▶ dezr-api ──InvokeAgentRuntime──▶ AgentCore ──▶ dezr-ai
                                               │ stores question + answer          │ searches documents
                                               ▼                                   ▼
                                       ai_sessions / ai_messages   POST /api/v1/documents/search (this API)
```

- **What the agent receives:** `{ message, sessionId, stream, agentId, context, history }`.
  dezr-ai reads `message` and `sessionId` (its LangGraph memory is keyed on it); the conversation
  id is also AgentCore's `runtimeSessionId`, so one conversation stays on one runtime session.
- **What comes back:** a stream of answer text, the model's thinking, the document search as a
  tool step, and a final object with the answer's citations (the documents it used), model and
  token usage. All of it is stored with the answer and relayed to the browser as server-sent events.
- **Storage:** a question and its answer are stored together only once the answer exists. A
  failed agent call (429/503) stores nothing, so the visitor can retry.
- **Limits** (model calls cost money and visitors are anonymous): `AI_RATE_LIMIT_PER_MINUTE` (10)
  messages per visitor per minute, `AI_DAILY_MESSAGE_LIMIT` (1000) for everyone per 24 hours,
  conversations of up to `AI_MAX_MESSAGES_PER_SESSION` (100) messages, 4000 characters each.
- **Who may call it:** with `AI_API_KEY` set, only callers sending `Authorization: Bearer <key>`,
  i.e. dezr-ui's server (`NUXT_CHAT_API_TOKEN`). Such a caller's `X-Forwarded-For` (its last
  entry) is taken as the visitor's address, so each visitor gets their own limit.

| Setting | Default | |
| --- | --- | --- |
| `AI_AGENT_RUNTIME_ARN` | – | dezr-ai's runtime (`arn:aws:bedrock-agentcore:<region>:<account>:runtime/<id>`). The AWS identity needs `bedrock-agentcore:InvokeAgentRuntime` on it. |
| `AI_AGENT_REGION` | `AWS_DEFAULT_REGION` | |
| `AI_AGENT_URL` | – | Instead of AgentCore: dezr-ai over HTTP (`uv run python main.py` in dezr-ai → `http://localhost:8080/invocations`; from Docker `http://host.docker.internal:8080/invocations`) |
| `AI_AGENT_TIMEOUT_SECONDS` | `120` | Longest wait between two pieces of the answer |
| `AI_API_KEY` | – | Bearer key for the chat endpoints; unset leaves them open (development) |
| `AI_RATE_LIMIT_PER_MINUTE` / `AI_DAILY_MESSAGE_LIMIT` / `AI_MAX_MESSAGES_PER_SESSION` | `10` / `1000` / `100` | `0` turns a limit off |

With neither `AI_AGENT_RUNTIME_ARN` nor `AI_AGENT_URL`, the chat endpoints answer 503 and the rest
of the API is unaffected.

```bash
curl -N localhost:8001/api/v1/ai/messages/stream -H 'Content-Type: application/json' \
  -d '{"content": "What does the firm do?"}'
```

## Admin accounts and sign-in

### Default admin

A new installation gets one admin, **`admin@deszr.com`**, so the admin area can be used straight
away. The Docker image creates it on start (`flask admin ensure-default`) while the database has
no admin at all; after that it never touches it again, so it never resets a password.

- **Password:** `DEFAULT_ADMIN_PASSWORD` if you set one (12+ characters; it is never printed).
  Otherwise a strong one is generated and printed **once** in the API's log:

  ```bash
  docker compose logs api | grep -A1 "Created the default admin"
  ```

  With Terraform it is `terraform output -raw admin_password`.
- **Another email:** `DEFAULT_ADMIN_EMAIL=you@example.com`. Set it empty to skip the default admin.
- **Change the password** after the first sign-in: `flask admin set-password admin@deszr.com`.
- Without Docker: `uv run flask admin ensure-default` (reads the same settings from `app/.env`).

### More admins

```bash
uv run flask admin create you@example.com      # asks for a password (12+ characters)
uv run flask admin list
uv run flask admin set-password you@example.com
uv run flask admin deactivate you@example.com
```

`POST /api/v1/admin/auth/login` returns a token; send it as `Authorization: Bearer <token>` to
every `/api/v1/admin/...` endpoint. Tokens are signed with `SECRET_KEY` and last
`ADMIN_SESSION_HOURS` (8). Signing out, changing the password or deactivating the account
revokes all of that admin's tokens. After `ADMIN_MAX_FAILURES` (5) wrong passwords within
`ADMIN_LOCKOUT_MINUTES` (15), the IP and the account are locked out for the rest of that window
(429 with `Retry-After`), the same rules as dezr-ui's current sign-in. A wrong email and a wrong
password take the same time and give the same answer.

The intended setup keeps the token off the browser: dezr-ui's server signs in, keeps the token in
its sealed session cookie and adds the header when it forwards admin requests. Set
`TRUSTED_PROXY_COUNT=1` then, so the lockout and the contact form limit count the visitor's IP
from `X-Forwarded-For` rather than the Nuxt server's.

| Setting | Default | |
| --- | --- | --- |
| `SECRET_KEY` | – | Required in production (admin sign-in answers 503 without it). Random per process in debug. |
| `ADMIN_SESSION_HOURS` | `8` | |
| `ADMIN_MAX_FAILURES` / `ADMIN_LOCKOUT_MINUTES` | `5` / `15` | |
| `TRUSTED_PROXY_COUNT` | `0` | Proxies in front that set `X-Forwarded-For`. Never more than really exist. |
| `CONTACT_RATE_LIMIT_PER_HOUR` | `5` | |
| `CORS_ORIGINS` | `http://localhost:3000` | Only needed when browsers call the API directly. |
| `MEDIA_PUBLIC_BASE_URL` | `/media` | Media URLs in responses are `<base>/<name>`. |

## Media storage (AWS S3)

Same design as vendor-portal-api's document storage:

- **Object keys:** `<S3_KEY_PREFIX>/media/<random>.<ext>`. The public file name is kept only in the database.
- **Security:** objects are private and encrypted at rest (SSE-S3 `AES256` by default, or `aws:kms`). Files are streamed through the API, so the bucket never needs to be public or have CORS.
- **Consistency:** if saving the record fails after the upload, the object is deleted again; deleting a file deletes its object. If S3 is down, uploads and downloads return 503.
- **Credentials:** the standard AWS chain (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, `AWS_PROFILE`, or the ECS task / EC2 instance role). The IAM identity needs `s3:PutObject`, `s3:GetObject` and `s3:DeleteObject` on `arn:aws:s3:::<bucket>/<prefix>/*`.

| Setting | Default | |
| --- | --- | --- |
| `STORAGE_BACKEND` | `s3` | `local` stores files on disk under `UPLOAD_FOLDER` (development only) |
| `S3_BUCKET` | – | Required for `s3` |
| `S3_REGION` | `AWS_DEFAULT_REGION` | |
| `S3_KEY_PREFIX` | `dezr` | |
| `S3_SERVER_SIDE_ENCRYPTION` | `AES256` | `aws:kms` with `S3_KMS_KEY_ID`, or empty for the bucket default |
| `S3_ENDPOINT_URL` | – | Only for S3-compatible emulators |

## Tests

```bash
uv sync
uv run pytest
```

The tests use SQLite by default. To run them against PostgreSQL with pgvector (the vector column,
indexes and row locking), point `TEST_DATABASE_URL` at an empty database; it is migrated down and
up for every test:

```bash
TEST_DATABASE_URL=postgresql://dezr:...@localhost:5437/dezr_test uv run pytest
```

## Run with Docker

```bash
cp .env.example .env              # then set POSTGRES_PASSWORD and SECRET_KEY
docker compose up -d --build      # Postgres (pgvector) + API on http://localhost:8001 + document worker
docker compose logs api | grep -A1 "Created the default admin"   # admin@deszr.com and its password
```

Set `S3_BUCKET` and the AWS credentials in `.env`. To try it without AWS, uncomment the "Local S3
emulator" lines in `.env` and start the stack with `docker compose --profile local-s3 up -d`, or set
`STORAGE_BACKEND=local`.

- The `worker` container processes uploaded documents (`INGESTION_MODE=worker`); it starts once the API is healthy, i.e. migrated.
- The API is published on host port **8001**, because vendor-portal-api (dezr-ui's chat backend) usually has 8000.
- On startup the container applies migrations (`RUN_MIGRATIONS=1`) and loads the shipped pages (`SEED_CONTENT=1`). With several replicas, set both to `0` and run `flask db upgrade` and `flask seed-content` once from a separate job.
- The app is served by gunicorn on port 8000 inside the container, as a non-root user. `GET /healthz` returns 503 when the database is unreachable.
- Data lives in named volumes: `db-data` (Postgres) and `uploads` (media, when `STORAGE_BACKEND=local`).
- `app/.env` is never copied into the image. Configuration comes from the environment.

## Deploy to AWS (Terraform)

[terraform/](terraform/README.md) runs this stack on one EC2 instance (`t4g.small`): PostgreSQL
with pgvector on its own EBS volume, the API on port 80 behind an Elastic IP, the document worker,
and S3 for uploads. `terraform apply` waits until the API answers, then prints its URL.

```bash
cd terraform && terraform init && terraform apply
```

## Local setup (without Docker)

1. Copy `app/.env.example` to `app/.env` and point `DATABASE_URL` at a PostgreSQL database with
   pgvector (e.g. `docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres pgvector/pgvector:pg17`).
2. Create the tables, load the shipped pages and add an admin:

   ```bash
   uv run flask db upgrade
   uv run flask seed-content
   uv run flask admin ensure-default   # admin@deszr.com; prints a generated password once
   ```

3. Run the server:

   ```bash
   uv run flask run
   ```

   Swagger UI: http://localhost:5000/swagger-ui

## Keeping the shipped content in sync

`app/seed/pages/*.md` is a copy of `dezr-ui/content/*.md`, and `app/seed/site_defaults.py` mirrors
`dezr-ui/shared/site/defaults.ts`. Once dezr-ui reads its content from this API, those files in
dezr-ui can go. Until then, `tests/test_seed.py` fails when the page copies drift apart (if
both repositories are checked out side by side).
