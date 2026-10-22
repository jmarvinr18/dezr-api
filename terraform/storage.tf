# ---------- S3: uploaded media and documents (dezr/), and the app bundle (deploy/) ----------

resource "random_id" "bucket" {
  byte_length = 4
}

resource "aws_s3_bucket" "main" {
  bucket        = "${var.name}-${random_id.bucket.hex}"
  force_destroy = var.force_destroy_bucket
}

resource "aws_s3_bucket_public_access_block" "main" {
  bucket                  = aws_s3_bucket.main.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "main" {
  bucket = aws_s3_bucket.main.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Admin > Documents sends files straight here from the browser (a presigned POST the API signs),
# so a large document never passes through dezr-ui's server. The bucket stays private: this only
# lets those pages make the request.
resource "aws_s3_bucket_cors_configuration" "main" {
  count  = length(var.upload_origins) > 0 ? 1 : 0
  bucket = aws_s3_bucket.main.id

  cors_rule {
    allowed_methods = ["POST"]
    allowed_origins = var.upload_origins
    allowed_headers = ["*"]
    max_age_seconds = 3000
  }
}

# A direct upload waits in incoming/ (the app's DOCUMENT_UPLOAD_PREFIX) until the API confirms it
# and moves it to documents/. One that is never confirmed, e.g. the admin closed the page, goes
# after a day.
resource "aws_s3_bucket_lifecycle_configuration" "main" {
  bucket = aws_s3_bucket.main.id

  rule {
    id     = "expire-unconfirmed-document-uploads"
    status = "Enabled"

    filter {
      prefix = "${local.media_prefix}/incoming/"
    }

    expiration {
      days = 1
    }
  }
}

# ---------- The app, as built on the instance ----------

locals {
  app_root = abspath("${path.module}/..")
  # Exactly what the Docker build needs: never .venv, tests, .pyc files or app/.env.
  bundle_files = sort(tolist(setunion(
    fileset(local.app_root, "app/**/*.py"),
    fileset(local.app_root, "app/seed/pages/*.md"),
    fileset(local.app_root, "migrations/**/*.py"),
    toset([
      "migrations/alembic.ini",
      "migrations/script.py.mako",
      "pyproject.toml",
      "uv.lock",
      "Dockerfile",
      ".dockerignore",
      "docker-compose.yml",
      "docker-entrypoint.sh",
    ]),
  )))
  # Every file with Linux line endings, whatever the checkout has: a script with a CRLF
  # shebang ("#!/bin/sh\r") can't run in the container.
  bundle_content = {
    for f in local.bundle_files : f => replace(file("${local.app_root}/${f}"), "\r\n", "\n")
  }
  # Changes when the code does (not its line endings), which redeploys the instance (see compute.tf).
  bundle_hash = substr(sha256(join("\n", [
    for f in local.bundle_files : "${f} ${sha256(local.bundle_content[f])}"
  ])), 0, 16)
  bundle_key = "deploy/dezr-api-${local.bundle_hash}.zip"
}

data "archive_file" "bundle" {
  type        = "zip"
  output_path = "${path.module}/.build/dezr-api-${local.bundle_hash}.zip"

  dynamic "source" {
    for_each = local.bundle_files
    content {
      filename = source.value
      content  = local.bundle_content[source.value]
    }
  }
}

resource "aws_s3_object" "bundle" {
  bucket      = aws_s3_bucket.main.id
  key         = local.bundle_key
  source      = data.archive_file.bundle.output_path
  source_hash = data.archive_file.bundle.output_base64sha256

  depends_on = [aws_s3_bucket_server_side_encryption_configuration.main]
}

# ---------- Secrets: generated here, read by the instance at boot (never in user data) ----------

resource "random_password" "postgres" {
  length  = 32
  special = false # it goes into DATABASE_URL
}

resource "random_password" "secret_key" {
  length  = 64
  special = false
}

# Bearer key of POST /api/v1/documents/search. dezr-ai's AgentCore runtime reads the same
# parameter, so the two never need the value copied between them.
resource "random_password" "document_search_key" {
  length  = 48
  special = false
}

resource "random_password" "admin" {
  count   = var.admin_email == null ? 0 : 1
  length  = 24
  special = false
}

locals {
  admin_password = var.admin_email == null ? null : coalesce(var.admin_password, random_password.admin[0].result)
}

resource "aws_ssm_parameter" "postgres_password" {
  name        = "/${var.name}/postgres-password"
  description = "PostgreSQL password of the dezr API stack"
  type        = "SecureString"
  value       = random_password.postgres.result
}

resource "aws_ssm_parameter" "secret_key" {
  name        = "/${var.name}/secret-key"
  description = "SECRET_KEY of the dezr API (signs admin tokens)"
  type        = "SecureString"
  value       = random_password.secret_key.result
}

resource "aws_ssm_parameter" "document_search_key" {
  name        = "/${var.name}/document-search-key"
  description = "DOCUMENT_SEARCH_API_KEY of the dezr API (dezr-ai's document retrieval)"
  type        = "SecureString"
  value       = random_password.document_search_key.result
}

# Bearer key of POST /api/v1/ai/messages[/stream]: dezr-ui's server sends it
# (NUXT_CHAT_API_TOKEN), so visitors can only reach the assistant through the site.
resource "random_password" "ai_api_key" {
  count   = var.ai_require_api_key ? 1 : 0
  length  = 48
  special = false
}

resource "aws_ssm_parameter" "ai_api_key" {
  count       = var.ai_require_api_key ? 1 : 0
  name        = "/${var.name}/ai-api-key"
  description = "AI_API_KEY of the dezr API (dezr-ui's NUXT_CHAT_API_TOKEN)"
  type        = "SecureString"
  value       = random_password.ai_api_key[0].result
}

# OpenAI key for the document embeddings (embedding_provider = "openai").
resource "aws_ssm_parameter" "openai_api_key" {
  count       = var.embedding_provider == "openai" && var.openai_api_key != null ? 1 : 0
  name        = "/${var.name}/openai-api-key"
  description = "OPENAI_API_KEY of the dezr API (document embeddings)"
  type        = "SecureString"
  value       = var.openai_api_key
}

resource "aws_ssm_parameter" "admin_password" {
  count       = var.admin_email == null ? 0 : 1
  name        = "/${var.name}/admin-password"
  description = "Password of the default dezr admin (${var.admin_email})"
  type        = "SecureString"
  value       = local.admin_password
}
