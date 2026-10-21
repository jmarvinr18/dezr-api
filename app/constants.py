"""Reference values shared with the Nuxt app (see dezr-ui/shared/site/schema.ts)."""

import re

# Sections of the site settings, each saved and reset on its own.
SETTINGS_SECTIONS = ["site", "contact", "nav", "slides", "mission", "banners", "footer", "assistant"]

# Slide art palettes drawn by dezr-ui's SkylineArt.vue.
SLIDE_SCENES = ["dusk", "night", "teal", "violet", "dawn"]
# Download banner designs drawn by dezr-ui's PromoBanner.vue.
BANNER_VARIANTS = ["en", "ja", "de", "fi"]
# "home" = mission box + download banners in the right column, "banners" = banners only.
PAGE_SIDEBARS = ["home", "banners"]

# A site path ("/contact") or an https / mailto / tel link, never javascript: and friends.
HREF_PATTERN = re.compile(r"^(/(?!/)|https?://|mailto:|tel:)", re.IGNORECASE)
# A page path: "/" or lower-case, hyphenated segments ("/partners-and-counsel").
PAGE_PATH_PATTERN = re.compile(r"^/(?:[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*)?$")
LANG_PATTERN = re.compile(r"^[a-z]{2}(-[A-Za-z]{2})?$")

PAGE_BODY_MAX_LENGTH = 100_000
# Pages the site can't do without; they can be edited but not deleted or unpublished.
REQUIRED_PAGES = ["/"]

# Uploaded media. SVG and HTML are left out on purpose: they can carry scripts.
MEDIA_EXTENSIONS = ["png", "jpg", "gif", "webp", "pdf"]
MEDIA_MAX_FILE_SIZE = 10 * 1024 * 1024
MEDIA_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.(?:png|jpg|gif|webp|pdf)$")

CONTACT_NAME_MAX_LENGTH = 200
CONTACT_EMAIL_MAX_LENGTH = 320
CONTACT_SUBJECT_MAX_LENGTH = 300
CONTACT_MESSAGE_MAX_LENGTH = 10_000

SEARCH_QUERY_MAX_LENGTH = 200

# ---------- Documents for the AI assistant (RAG ingestion) ----------

# processing: stored, being chunked and embedded; ready: searchable; failed: see `error`.
DOCUMENT_STATUSES = ["processing", "ready", "failed"]
DOCUMENT_KINDS = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
    "md": "text/markdown",
}
DOCUMENT_MAX_FILE_SIZE = 25 * 1024 * 1024
DOCUMENT_TITLE_MAX_LENGTH = 200
# Direct uploads (POST /admin/documents/uploads): how long the browser has to send the file to
# S3, and where it waits until it is confirmed. terraform/storage.tf expires anything left there.
DOCUMENT_UPLOAD_EXPIRES_SECONDS = 15 * 60
DOCUMENT_UPLOAD_PREFIX = "incoming"
# Vector size of the embedding column. Titan Text Embeddings V2 (at 1024) and Cohere Embed v3
# both produce 1024; changing it needs a migration and re-embedding every document.
EMBEDDING_DIMENSIONS = 1024
# POST /api/v1/documents/search
DOCUMENT_SEARCH_QUERY_MAX_LENGTH = 1000
DOCUMENT_SEARCH_MAX_RESULTS = 20

# ---------- AI assistant (chat relayed to the dezr-ai agent on Bedrock AgentCore) ----------

AI_MESSAGE_MAX_LENGTH = 4000
# Kinds of source an answer may cite (the ones dezr-ui's chat shows): dezr-ai cites documents.
AI_CITATION_TYPES = ["document", "policy"]
