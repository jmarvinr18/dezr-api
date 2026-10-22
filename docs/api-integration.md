# Integration guide

How dezr-ui (or any other client) works with this API.

- **Part 1 — [Switching dezr-ui over](#part-1--switching-dezr-ui-over)**: which UI file reads what
  today, what it calls instead, and how the admin sign-in fits in.
- **Part 2 — [API reference](#part-2--api-reference)**: every endpoint, with request and response shapes.

Interactive docs are served by the running API at `/swagger-ui`, and the raw spec at
`/openapi.json`.

## Conventions

| | |
| --- | --- |
| Base path | `/api/v1` |
| JSON keys | `camelCase` in and out. Unknown keys in a request body are rejected with 422. |
| Text | Trimmed. Required text can't be blank; optional text defaults to `""`. |
| Timestamps | ISO 8601 with a UTC offset (e.g. `2026-10-20T06:46:51.642352+00:00`) |
| Identifiers | UUIDs as strings; media files are addressed by their name |
| Links | A site path (`/contact`) or `https://`, `mailto:`, `tel:`. `javascript:` and `//host` are refused. |

### Authentication

Public endpoints need nothing. Every `/api/v1/admin/...` endpoint except `auth/login` needs
`Authorization: Bearer <token>`, where the token comes from signing in:

```bash
TOKEN=$(curl -s localhost:8001/api/v1/admin/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"..."}' | jq -r .token)
curl localhost:8001/api/v1/admin/pages -H "Authorization: Bearer $TOKEN"
```

A missing, expired or revoked token is `401`. Tokens last `ADMIN_SESSION_HOURS` (8); signing out
revokes every token the admin holds.

### Errors

Every error carries `code` and `status`. There are two shapes, as in vendor-portal-api.

**Domain errors** (business rules) carry a human-readable `message`, and `errors` when there is
per-field detail (flat `{field: message}`):

```json
{ "code": 409, "status": "Conflict", "message": "Another page already uses /contact.", "errors": { "path": "This path is taken." } }
```

**Request-schema errors** (a malformed body or query) come from the framework: no `message`, and
`errors` nested by location, then by field, with list indexes as keys:

```json
{
  "code": 422,
  "status": "Unprocessable Entity",
  "errors": { "json": { "slides": { "2": { "title": ["Required."] } }, "nav": { "1": { "to": ["Use a site path (/page) or a full https:// link."] } } } }
}
```

dezr-ui's admin forms key problems by dotted path (`slides.2.title`). This turns the nested shape
into that:

```ts
function flattenIssues(errors: unknown, prefix = ''): Record<string, string> {
  if (Array.isArray(errors)) return { [prefix]: String(errors[0]) }
  if (!errors || typeof errors !== 'object') return {}
  return Object.assign({}, ...Object.entries(errors).map(([k, v]) => flattenIssues(v, prefix ? `${prefix}.${k}` : k)))
}
// flattenIssues(error.data.errors.json) → { "slides.2.title": "Required.", ... }
```

| Status | Means |
| --- | --- |
| `401` | Admin endpoint without a valid token, or a wrong email / password |
| `404` | Does not exist (or is unpublished, for public pages) |
| `409` | Conflicts with the current state (path taken, home page protected, nothing to reset to) |
| `413` | Upload too large (media 10 MB, documents 25 MB) |
| `415` | A document that is not a PDF, .docx, .txt or .md (judged by its content) |
| `422` | Failed validation; nothing was stored |
| `429` | Sign-in lockout or contact-form limit; see `Retry-After` |
| `503` | S3 is unavailable, or `SECRET_KEY` is not set (admin sign-in) |

### CORS

Browsers may call the API directly only from an origin listed in `CORS_ORIGINS`. Allowed request
headers are `Accept`, `Authorization` and `Content-Type`; `Content-Disposition` and `Retry-After`
are exposed. With the setup below, the browser only ever talks to the Nuxt site, so CORS is not
needed in production.

---

# Part 1 — Switching dezr-ui over

Today dezr-ui reads its content from files in its own repository (`content/*.md`,
`shared/site/defaults.ts`) and keeps admin edits in Nitro storage (`.data/cms`). With this API,
the API is the single source of truth and the Nuxt server becomes a thin layer in front of it,
the same way it forwards `/api/v1/ai/**` (the assistant's chat), which this API now answers too.

```
Browser ──▶ Nuxt server (dezr-ui) ──▶ dezr-api ──▶ PostgreSQL, S3
              │  adds Authorization (admin token kept in its sealed cookie)
              │  adds X-Forwarded-For (visitor's IP, for lockout / rate limit)
              │  renders + sanitises page Markdown (renderMarkdown)
```

Nuxt settings to add (names are suggestions): `NUXT_CONTENT_API` (the API origin, e.g.
`http://localhost:8001`). On the API, set `TRUSTED_PROXY_COUNT=1` so it takes the visitor's IP
from the `X-Forwarded-For` the Nuxt server sends.

## Public site

| dezr-ui file | Reads today | Calls instead |
| --- | --- | --- |
| `app/plugins/cms-settings.ts` → `useAppConfig()` in `SiteHeader`, `SiteNav`, `HeroSlideshow`, `SiteSidebar`, `SidebarMission`, `PromoBanner`, `SiteFooter`, `ContactSection`, `JapanDeskEmail`, the chat components | `/api/cms/settings` (null until an admin saves) | `GET /api/v1/site-settings` — always the full settings, so `applySiteSettings()` can apply it as is |
| `app/pages/[...slug].vue` | `queryCollection('pages').path()` + `/api/cms/page` | `GET /api/v1/pages/by-path?path=<route.path>`; 404 → the 404 page |
| `server/api/cms/page.get.ts` | Nitro storage | Fetch the page from the API and return `{ ...fields, ast: await renderMarkdown(page.body) }`. The page component then always uses `<MDCRenderer>`, and the Markdown is sanitised on the server exactly as admin edits are today. |
| `app/components/content/SiteMap.vue` | `queryCollection('pages')` | `GET /api/v1/pages` (published, in site-map order) |
| `app/pages/search.vue` | `queryCollectionSearchSections('pages')` | `GET /api/v1/search?q=` — same `{id, title, titles, content}` shape, filtering already done |
| `server/routes/media/[name].get.ts` | Nitro storage | Proxy `GET /api/v1/media/<name>` (keeps `/media/<name>` URLs working) |
| `server/api/contact.post.ts` | Logs in dev, 503 in production | Forward the body to `POST /api/v1/contact-messages` (the form's fields already match, `copy` included) with `X-Forwarded-For` |

Once this is done, `@nuxt/content`, `content.config.ts`, `content/*.md`, `server/utils/cms.ts`'s
storage functions and the `cms` Nitro storage mount can go; `renderMarkdown()` and its cleaning
stay.

## Admin area

Keep the admin's browser-facing routes (`/api/admin/**`) and have them call the API, so the
pages under `app/pages/admin/` barely change and the token never reaches the browser:

| dezr-ui route | API call | Notes |
| --- | --- | --- |
| `POST /api/admin/login` | `POST /api/v1/admin/auth/login` | Store `token` in the sealed `dz_admin` session cookie (`useAdminSession`) instead of the email. Pass the 401 / 429 (and `Retry-After`) through. `NUXT_ADMIN_USERS`, `password.ts` and the in-memory lockout are no longer needed; accounts are made with `flask admin create`. |
| `GET /api/admin/session`, `server/middleware/admin.ts` | `GET /api/v1/admin/auth/me` | A 401 means the session ended: clear the cookie. |
| `POST /api/admin/logout` | `POST /api/v1/admin/auth/logout` | Then clear the cookie. |
| `GET /api/admin/settings` | `GET /api/v1/admin/site-settings` | Same `{settings, defaults, revision}`, plus `editedSections`. |
| `PUT /api/admin/settings` | `PUT /api/v1/admin/site-settings` | 422 errors: see `flattenIssues` above. `PATCH` can save one tab's sections only. |
| `DELETE /api/admin/settings` | `DELETE /api/v1/admin/site-settings` | `DELETE .../{section}` resets a single section. |
| `GET /api/admin/pages` | `GET /api/v1/admin/pages` | Adds `id`, `published`, `hasDefault`. Link the editor by `id` instead of `path`. |
| `GET /api/admin/page?path=` | `GET /api/v1/admin/pages/{id}` | The UI's `original` is the API's `default`; its `edit` is the page itself when `edited` is true. |
| `PUT /api/admin/page?path=` | `PATCH /api/v1/admin/pages/{id}` | Sends only what changed (sending every field is fine too). |
| `DELETE /api/admin/page?path=` (reset) | `POST /api/v1/admin/pages/{id}/reset` | Restores the shipped text, options and path. |
| `POST /api/admin/preview` | — | Stays in Nuxt: it renders Markdown, which is the UI's job. |
| `GET/POST /api/admin/media`, `DELETE /api/admin/media/{name}` | `GET/POST /api/v1/admin/media`, `DELETE .../{name}` | Same fields (`name`, `type`, `size`, `uploadedAt`, `uploadedBy`, `url`). Forward the multipart body as is. |
| — | `POST /api/v1/admin/pages`, `DELETE /api/v1/admin/pages/{id}` | New: create and delete pages. |
| — | `GET /api/v1/admin/contact-messages`, `GET/DELETE .../{id}` | New: an inbox for contact-form enquiries. |

---

# Part 2 — API reference

## Public

### `GET /api/v1/site-settings`

Everything the site's layout shows. Sections nobody changed come back as shipped.

```json
{
  "site": { "name": "Donato & Zarate Law Firm", "logo": { "title": "Donato & Zarate", "tagline": "Attorneys and Counselors at Law" }, "description": "…", "url": "https://www.deszr.com" },
  "contact": { "addressLines": ["7/F Electra House, …", "1229 Makati City, Philippines"], "email": "admin@deszr.com", "japanDeskEmail": "japandesk@deszr.com", "phone": "+632-8-892-7288", "fax": "+632-8-893-2764", "hours": "Monday - Friday, …" },
  "nav": [{ "label": "Home", "to": "/", "home": true }, { "label": "Partners and Counsel", "to": "/partners-and-counsel", "home": false }],
  "slides": [{ "title": "Corporation", "caption": "…", "scene": "dusk", "headline": "Counsel", "headline2": "Clarity", "tagline": "…" }],
  "mission": { "title": "Our Mission", "text": "…", "link": { "label": "More info", "to": "/partners-and-counsel" } },
  "banners": [{ "variant": "en", "kicker": "", "title": "Doing Business", "title2": "in the Philippines", "action": "Download", "url": "/downloads/….pdf", "lang": "en" }],
  "footer": { "columns": [{ "title": "About Us", "links": [{ "label": "Library", "to": "/library" }] }] },
  "assistant": { "title": "AI Assistant", "name": "Firm Assistant", "subtitle": "…", "greeting": "How can I help?", "intro": "…", "placeholder": "Ask a question…", "prompts": ["…"], "disclaimer": "…" }
}
```

### `GET /api/v1/pages`

Published pages in site-map order: `[{ path, title, lang, order, parent: {title, to} | null }]`.

### `GET /api/v1/pages/by-path?path=/contact`

One published page. `?path=/` is the home page; a trailing slash is ignored. Unknown, malformed
or unpublished paths are `404`.

```json
{
  "path": "/work-with-us", "title": "Work with us", "heading": "Work with us",
  "description": "Careers at the firm.", "lang": "en",
  "slideshow": true, "sidebar": "home", "tools": false,
  "parent": { "title": "About us", "to": null }, "order": 8,
  "body": "We are not recruiting for associate or staff positions right now. …",
  "updatedAt": "2026-10-20T06:46:31.334517+00:00"
}
```

`body` is Markdown with the site's components, returned as written. **Render it with a sanitising
renderer** (dezr-ui's `renderMarkdown()`); never inject it as HTML.

### `GET /api/v1/search?q=visa`

Sections of published pages containing every word (case-insensitive), at most 50, in site-map
order: `[{ id: "/#immigration", title: "Immigration", titles: ["Law Office …"], level: 2, content: "We obtain resident visas …" }]`.
An empty `q` returns `[]`.

### `GET /api/v1/media/{name}`

The file's bytes, `Content-Disposition: inline`, cached 5 minutes, with `X-Content-Type-Options: nosniff`
and a sandboxing `Content-Security-Policy`. `404` for unknown names.

### `POST /api/v1/contact-messages`

```json
{ "name": "Juan dela Cruz", "email": "juan@example.com", "subject": "Incorporation", "message": "…", "copy": false }
```

`201 { id, receivedAt }`. `422` lists invalid fields; `429` after `CONTACT_RATE_LIMIT_PER_HOUR`
messages from one visitor in an hour.

### `GET /api/v1/reference-data`

Options for the admin forms: `settingsSections`, `slideScenes`, `bannerVariants`, `pageSidebars`,
`pageBodyMaxLength`, and `media: { acceptedExtensions, maxFileSize }`.

## Admin — sign-in

| Method | Path | |
| --- | --- | --- |
| POST | `/admin/auth/login` | `{ email, password }` → `{ token, expiresAt, user: { id, email, lastLoginAt } }`. `401` wrong email or password (same answer for both), `429` locked out (`Retry-After`), `503` no `SECRET_KEY`. |
| POST | `/admin/auth/logout` | `204`. Revokes every token this admin holds. |
| GET | `/admin/auth/me` | `{ id, email, lastLoginAt }` |

## Admin — site settings

All return `{ settings, defaults, revision: { updatedAt, updatedBy } | null, editedSections: [...] }`.

| Method | Path | |
| --- | --- | --- |
| GET | `/admin/site-settings` | |
| PUT | `/admin/site-settings` | The whole settings document (every section required). |
| PATCH | `/admin/site-settings` | Only some sections, e.g. `{ "banners": [...] }`. Each section sent is replaced whole. |
| DELETE | `/admin/site-settings` | Back to the shipped settings. |
| DELETE | `/admin/site-settings/{section}` | Back to one section's shipped settings. `404` for an unknown section. |

Validation follows dezr-ui's `siteSettingsSchema`: lengths, 1–12 menu items and slides, up to 8
banners, 4 footer columns of 12 links and 6 assistant prompts, `scene` in
`dusk | night | teal | violet | dawn`, `variant` in `en | ja | de | fi`, `lang` like `en` or `pt-BR`,
and every link a site path or `https` / `mailto` / `tel` link.

## Admin — pages

| Method | Path | |
| --- | --- | --- |
| GET | `/admin/pages` | Every page: `{ id, path, title, order, published, edited, hasDefault, updatedAt, updatedBy }` |
| POST | `/admin/pages` | Create. `path` and `title` required; the rest default (`lang: "en"`, `sidebar: "banners"`, `tools: true`, `order: 99`, `published: true`). `409` when the path is taken. |
| GET | `/admin/pages/{id}` | The page (all fields) plus `default`: its shipped version, or `null` for pages admins created. |
| PATCH | `/admin/pages/{id}` | Change only the fields sent. Returns the same as GET. |
| DELETE | `/admin/pages/{id}` | `204` |
| POST | `/admin/pages/{id}/reset` | Restore the shipped version (text, options and path). `409` for pages admins created. |

Page fields: `path` (`/` or lower-case words joined by hyphens, e.g. `/about-us/team`), `title`
(120), `heading` (120), `description` (300), `lang`, `slideshow`, `sidebar` (`home | banners`),
`tools`, `parent` (`{ title, to? }` or `null`), `order` (0–10000), `published`, `body` (Markdown,
100,000 characters). The home page can't be deleted, moved or unpublished (`409`).

## Admin — media

| Method | Path | |
| --- | --- | --- |
| GET | `/admin/media` | `[{ id, name, url, type, size, uploadedAt, uploadedBy }]`, newest first |
| POST | `/admin/media` | multipart/form-data, one `file` part. `201` with the file. `413` over 10 MB, `422` not a PNG / JPG / GIF / WebP / PDF (by content), `503` S3 down. |
| DELETE | `/admin/media/{name}` | `204` |

`url` is `MEDIA_PUBLIC_BASE_URL/<name>` (`/media/<name>` by default), the address to put in pages
and banners.

## Admin — contact messages

| Method | Path | |
| --- | --- | --- |
| GET | `/admin/contact-messages?page=1&pageSize=20` | `{ items: [{ id, name, email, subject, message, copy, receivedAt }], total, page, pageSize, pageCount }`, newest first |
| GET | `/admin/contact-messages/{id}` | One message |
| DELETE | `/admin/contact-messages/{id}` | `204` |

## AI assistant chat

The chat dezr-ui shows (`server/routes/api/v1/ai/[...path].ts` forwards to these with
`NUXT_CHAT_UPSTREAM` = this API's origin and `NUXT_CHAT_API_TOKEN` = `AI_API_KEY`). The answers
come from the dezr-ai agent on Bedrock AgentCore. Same request and response shapes as
vendor-portal-api's, so `app/types/chat.ts` and the chat client need no changes.

| Method | Path | |
| --- | --- | --- |
| POST | `/ai/messages` | `{ content, sessionId?, agentId?, context?: { page? } }` → `201 { sessionId, userMessage, assistantMessage }` |
| POST | `/ai/messages/stream` | Same body; the answer as server-sent events (below) |

Omit `sessionId` to start a conversation; send the returned one to continue it. A message is up
to 4000 characters. With `AI_API_KEY` set, both need `Authorization: Bearer <AI_API_KEY>`, and the
caller's `X-Forwarded-For` (last entry) is taken as the visitor's address for the rate limit, so
dezr-ui's proxy should forward it.

```
event: thinking  data: {"text": "…"}                         the model's reasoning
event: tool      data: {id, name, status, input, output}    a document search: started, then
                                                            completed / failed (same id)
event: delta     data: {"text": "…"}                         the answer, piece by piece
event: done      data: {sessionId, userMessage, assistantMessage}
event: error     data: {"code": 503, "message": "…"}         if it fails mid-answer
```

`assistantMessage` carries `content`, `citations` (`[{type: "document", id, label}]`: the
documents the answer came from), `thinking` and `steps`. Errors before the first event are plain
HTTP errors: `401` no or wrong key, `404` unknown conversation, `409` conversation full (100
messages), `422` empty or too long, `429` too many messages (per visitor per minute, or for
everyone per day) or the agent throttled, `503` the agent unavailable or not configured.

## Admin — documents (RAG ingestion)

The files the AI assistant can search. dezr-ui's Admin > Documents page uses these (its contract
is `dezr-ui/docs/document-ingestion-api.md`; this API implements it as written).

| Method | Path | |
| --- | --- | --- |
| GET | `/admin/documents` | `Document[]`, newest first. Poll while any is `processing`. |
| POST | `/admin/documents` | multipart/form-data: `file` (required), `title` (optional, up to 200 characters; defaults to the file name without its extension). `202` with the document as `processing`. |
| POST | `/admin/documents/uploads` | JSON `{"size": <bytes>}`. `201` `{uploadId, url, fields, expiresAt, maxSize}`: a presigned S3 form for sending the file without passing through dezr-ui's server (Amplify Hosting refuses bodies over about 4.5 MB). `413` over 25 MB, `501` with local storage (use the multipart POST). |
| POST | `/admin/documents/uploads/{uploadId}/complete` | JSON `{"fileName": "…", "title": "…"}` (title optional), once the file is in S3. Same checks and `202` as the multipart POST; `404` if no file arrived (or it expired, or was already confirmed). A file failing the checks (`413`, `415`) is deleted; after a `422` (title, fileName) it is kept for a retry. |
| GET | `/admin/documents/{id}` | One document |
| DELETE | `/admin/documents/{id}` | `204`. Removes the file, its passages and their vectors; cancels processing still running. Allowed in any status. |
| GET | `/admin/documents/{id}/chunks?page=1&pageSize=50` | The passages, in reading order (for checking the chunking) |

```json
{
  "id": "6f1d1c2e-8b7d-4e21-9a0f-5c4b3a2d1e0f",
  "title": "Doing business in the Philippines",
  "fileName": "doing-business-ph.pdf",
  "contentType": "application/pdf",
  "size": 482113,
  "status": "ready",
  "chunkCount": 37,
  "error": null,
  "uploadedAt": "2026-10-20T09:12:44.120000+00:00",
  "uploadedBy": "admin@example.com",
  "processedAt": "2026-10-20T09:13:02.551000+00:00"
}
```

`status` is `processing` (stored, being chunked and embedded), `ready` (searchable; `chunkCount`
passages) or `failed` (`error` says why, in words an admin can act on: "The PDF has no readable
text. It may be a scan; …", "The PDF is password-protected. …", "The embedding service is
unavailable, …").

Sending the file to the form (the browser, `fetch` or `XMLHttpRequest`): a `multipart/form-data`
POST to `url` with every entry of `fields` first and the file last, in a part named `file`. S3
answers `204`; a `403` means the form expired (start again), a `400 EntityTooLarge` that the file
is over `maxSize`. The bucket's CORS rule (`upload_origins` in Terraform) must list the page's
origin.

A passage (`/chunks` items):

```json
{
  "index": 4,
  "content": "| Tax | Rate |\n| --- | --- |\n| Corporate income tax | 25% |\n| VAT | 12% |",
  "headingPath": ["Doing Business in the Philippines", "Taxation"],
  "pageStart": 3,
  "pageEnd": 3,
  "tokenCount": 31,
  "blockTypes": ["table"],
  "hasEmbedding": true
}
```

Errors: `413` over 25 MB, `415` not a PDF / .docx / .txt / .md by content (an old `.doc` gets a
message to save it as `.docx`), `422` title too long (`errors: { "title": "…" }`) or no `file`,
`503` the file store is down (nothing stored), `404` unknown id.

### Reading the passages for retrieval

### `POST /api/v1/documents/search` (the assistant's retrieval)

dezr-ai's agent searches the passages through this endpoint. It needs
`Authorization: Bearer <DOCUMENT_SEARCH_API_KEY>` (not an admin token): `401` without the right
key, `503` while `DOCUMENT_SEARCH_API_KEY` is unset. The key is checked before the body.

```json
{ "query": "How long do refunds take?", "limit": 5 }
```

`query` is required (up to 1000 characters); `limit` is 1-20 (default 5). `422` otherwise.

```json
{
  "mode": "hybrid",
  "items": [
    {
      "documentId": "6f1d1c2e-8b7d-4e21-9a0f-5c4b3a2d1e0f",
      "chunkId": "0b8e4c1a-2f3d-4e5a-9b6c-7d8e9f0a1b2c",
      "title": "Refund policy",
      "fileName": "refunds.pdf",
      "headingPath": ["Refund policy", "Refunds"],
      "pageStart": 2,
      "pageEnd": 2,
      "content": "Refunds are paid within 30 days of delivery.",
      "score": 0.0325
    }
  ]
}
```

Passages of `ready` documents only, best first. It runs the two queries below and merges them
with reciprocal rank fusion; vectors only count for documents embedded with the API's current
model. `mode` is `keyword` when the query couldn't be embedded (`EMBEDDING_PROVIDER=none`, or
the embedding service unreachable). PostgreSQL only (`503` on SQLite).

With `RETRIEVAL_BACKEND=bedrock_kb` the Bedrock Knowledge Base answers instead and `mode` is
`knowledge_base`: same fields, but `chunkId` is `null`, `headingPath` is empty, `pageStart` /
`pageEnd` are the page the knowledge base reports (PDFs), and `score` is its relevance score. If
the knowledge base can't be reached, the keyword ranking answers (`mode: keyword`).

### Reading the passages directly

The same retrieval, in SQL, for anything that reads the database itself (dezr-ai does this in
local development):

```sql
-- Semantic: nearest passages to the question's vector (embed it with the same model,
-- documents.embedding_model). Uses the HNSW index.
SELECT c.id, c.content, c.heading_path, c.page_start, d.title
FROM document_chunks c JOIN documents d ON d.id = c.document_id
WHERE d.status = 'ready'
ORDER BY c.embedding <=> :question_vector
LIMIT 20;

-- Keyword: exact terms, names and numbers the vectors can miss. Uses the GIN index; the
-- expression must match it exactly.
SELECT c.id, ts_rank(to_tsvector('simple', c.content), q) AS rank
FROM document_chunks c JOIN documents d ON d.id = c.document_id,
     websearch_to_tsquery('simple', :question) q
WHERE d.status = 'ready' AND to_tsvector('simple', c.content) @@ q
ORDER BY rank DESC
LIMIT 20;
```

Embed the question with the same model as the passages: Cohere with `input_type: "search_query"`
(the passages use `search_document`), or Titan's `inputText` as is. Show
`d.title`, `c.heading_path` and `c.page_start` with an answer so it can cite its source.
