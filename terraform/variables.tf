variable "aws_region" {
  description = "Region to deploy to. ap-southeast-1 (Singapore) is closest to Manila."
  type        = string
  default     = "ap-southeast-1"
}

variable "name" {
  description = "Prefix for resource names, the S3 bucket and the SSM parameters."
  type        = string
  default     = "dezr-api"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}[a-z0-9]$", var.name))
    error_message = "Use 3-22 lower-case letters, digits and hyphens, starting with a letter."
  }
}

variable "instance_type" {
  description = <<-EOT
    EC2 instance type. t4g.small (2 vCPU Graviton, 2 GiB) is the cheapest that comfortably runs
    the API, the document worker and PostgreSQL, and builds the image on boot. t4g.micro (1 GiB)
    works for light use but swaps while processing PDFs. x86 types (t3a.small, ...) work too.
  EOT
  type        = string
  default     = "t4g.small"
}

variable "root_volume_size" {
  description = "Root disk in GiB (OS, Docker images, build cache)."
  type        = number
  default     = 20
}

variable "data_volume_size" {
  description = "Separate disk in GiB for the PostgreSQL data. It outlives the instance, so redeploys keep the database."
  type        = number
  default     = 10
}

variable "snapshot_data_on_destroy" {
  description = "Take a snapshot of the database disk when it is destroyed (terraform destroy)."
  type        = bool
  default     = true
}

variable "vpc_cidr" {
  description = "Address range of the VPC created for the API."
  type        = string
  default     = "10.42.0.0/16"
}

variable "allowed_cidrs" {
  description = <<-EOT
    IPv4 ranges allowed to reach the API on port 80. Open to everyone by default so the API works
    straight away; narrow it to the dezr-ui server (and your own IP) once you know them.
  EOT
  type        = list(string)
  default     = ["0.0.0.0/0"]

  validation {
    condition     = alltrue([for cidr in var.allowed_cidrs : can(cidrhost(cidr, 0)) && !strcontains(cidr, ":")])
    error_message = "Use IPv4 CIDR blocks, e.g. 203.0.113.10/32."
  }
}

variable "ssh_public_key_path" {
  description = <<-EOT
    Turns on SSH: path to your OpenSSH public key (e.g. "~/.ssh/dezr-api-debug.pub"). Terraform
    registers it as an EC2 key pair; the private key never leaves your machine. Log in as
    ec2-user (see the ssh_command output). Changing the key replaces the instance, since EC2
    only installs it at launch. Null (and no ssh_key_name) leaves SSH off; Session Manager
    (the shell output) works either way.
  EOT
  type        = string
  default     = null
}

variable "ssh_key_name" {
  description = "Instead of ssh_public_key_path: the name of an EC2 key pair that already exists in this region."
  type        = string
  default     = null
}

variable "ssh_allow_my_ip" {
  description = "With SSH on, open port 22 to the public IP that runs terraform apply (looked up at https://checkip.amazonaws.com on each run)."
  type        = bool
  default     = true
}

variable "ssh_cidrs" {
  description = "More IPv4 ranges allowed to SSH in (with SSH on), e.g. a colleague's IP as \"203.0.113.10/32\"."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for cidr in var.ssh_cidrs : can(cidrhost(cidr, 0)) && !strcontains(cidr, ":")])
    error_message = "Use IPv4 CIDR blocks, e.g. 203.0.113.10/32."
  }
}

variable "cors_origins" {
  description = "Browser origins allowed to call the API directly (CORS_ORIGINS). Not needed when only the dezr-ui server calls it."
  type        = list(string)
  default     = ["http://localhost:3000"]
}

variable "upload_origins" {
  description = <<-EOT
    Browser origins allowed to send documents straight to the bucket (dezr-ui's Admin >
    Documents). Amplify Hosting refuses request bodies over about 4.5 MB, so the page uploads to
    S3 with a presigned form the API signs for a signed-in admin; this CORS rule only lets those
    pages make the request. An empty list removes the rule (uploads then go through the API).
  EOT
  type        = list(string)
  default     = ["https://main.d2c63rktpa3d5s.amplifyapp.com", "http://localhost:3000"]

  validation {
    condition     = alltrue([for origin in var.upload_origins : can(regex("^https?://[^/]+$", origin))])
    error_message = "Use origins without a path or trailing slash, e.g. \"https://www.deszr.com\"."
  }
}

variable "trusted_proxy_count" {
  description = <<-EOT
    Proxies in front of the API that set X-Forwarded-For (TRUSTED_PROXY_COUNT). Keep 0 while the
    API is open to everyone; set 1 once allowed_cidrs only admits the dezr-ui server, so sign-in
    lockouts and contact-form limits count visitors rather than that server.
  EOT
  type        = number
  default     = 0
}

variable "embedding_provider" {
  description = <<-EOT
    Vectors for uploaded documents (with retrieval_backend = "pgvector"):
    - "openai": OpenAI's embeddings API, with openai_api_key;
    - "bedrock": Amazon Bedrock (the instance role gets bedrock:InvokeModel on the model);
    - "none": keyword search only.
    Change it (and embedding_model_id) whenever you like: apply redeploys, and the document
    worker embeds the existing documents again with the new model when it starts.
  EOT
  type        = string
  default     = "openai"

  validation {
    condition     = contains(["openai", "bedrock", "none"], var.embedding_provider)
    error_message = "Use \"openai\", \"bedrock\" or \"none\"."
  }
}

variable "embedding_model_id" {
  description = <<-EOT
    Model for document vectors (asked for 1024 dimensions); null uses the provider's default.
    - openai: "text-embedding-3-small" (default) or "text-embedding-3-large".
    - bedrock: "cohere.embed-multilingual-v3" (default; in-Region in ap-southeast-1, sold through
      AWS Marketplace: an admin must call it once before the instance role can, see README.md,
      "Document embeddings") or "amazon.titan-embed-text-v2:0" (not offered in ap-southeast-1:
      set embedding_region to a Region that has it).
  EOT
  type        = string
  default     = null
}

variable "openai_api_key" {
  description = <<-EOT
    OpenAI API key for embedding_provider = "openai". Stored as an SSM SecureString (it is also
    in the Terraform state, like the other secrets). Pass it without writing it to a file:
    TF_VAR_openai_api_key=sk-... terraform apply. Changing it redeploys.
  EOT
  type        = string
  default     = null
  sensitive   = true
}

variable "retrieval_backend" {
  description = <<-EOT
    Where documents become searchable for the assistant:
    - "pgvector": the instance's PostgreSQL (vectors from embedding_provider + full-text search);
    - "bedrock_kb": an existing Bedrock Knowledge Base (knowledge_base_id), which chunks and
      embeds with its own model. Uploads are copied to its S3 data source and indexed one by
      one; dezr-ai's search goes through the API either way.
    Switching redeploys, and the worker re-indexes the existing documents when it starts.
  EOT
  type        = string
  default     = "pgvector"

  validation {
    condition     = contains(["pgvector", "bedrock_kb"], var.retrieval_backend)
    error_message = "Use \"pgvector\" or \"bedrock_kb\"."
  }
}

variable "knowledge_base_id" {
  description = "The Bedrock Knowledge Base for retrieval_backend = \"bedrock_kb\" (e.g. \"ABCDEFGHIJ\")."
  type        = string
  default     = null
}

variable "knowledge_base_data_source_id" {
  description = "Its S3 data source, which must read knowledge_base_s3_uri."
  type        = string
  default     = null
}

variable "knowledge_base_s3_uri" {
  description = <<-EOT
    Where the API copies documents for the knowledge base: the S3 data source's location,
    "s3://bucket/prefix/". null uses this deployment's bucket, under knowledge-base/ (output
    knowledge_base_s3_uri); point the data source there. Another bucket must allow the role
    s3:PutObject and s3:DeleteObject.
  EOT
  type        = string
  default     = null
}

variable "knowledge_base_region" {
  description = "Region of the knowledge base; null uses aws_region."
  type        = string
  default     = null
}

variable "knowledge_base_search_type" {
  description = "null for the knowledge base's default, or \"HYBRID\" / \"SEMANTIC\" if its vector store supports it."
  type        = string
  default     = null
}

variable "embedding_region" {
  description = "Region to call Bedrock in; null uses aws_region. Pick one that offers embedding_model_id."
  type        = string
  default     = null
}

variable "admin_email" {
  description = <<-EOT
    The default admin, created on first boot while the database has no admin yet (sign in to
    dezr-ui's /admin with it). Its password is admin_password, or generated: see
    `terraform output -raw admin_password`. Null to skip.
  EOT
  type        = string
  default     = "admin@deszr.com"

  validation {
    condition     = var.admin_email == null || can(regex("^[^@\\s'\"]+@[^@\\s'\"]+\\.[^@\\s'\"]+$", var.admin_email))
    error_message = "Enter a valid email address."
  }
}

variable "admin_password" {
  description = <<-EOT
    Password for the default admin (12-200 characters). Null generates a strong one. Only used
    when the admin is first created; change it later with `flask admin set-password`.
  EOT
  type        = string
  default     = null
  sensitive   = true

  validation {
    condition = var.admin_password == null || (
      length(coalesce(var.admin_password, "x")) >= 12 &&
      length(coalesce(var.admin_password, "x")) <= 200 &&
      !can(regex("['\\n\\r]", coalesce(var.admin_password, "x")))
    )
    error_message = "Use 12 to 200 characters, without single quotes or line breaks."
  }
}

variable "force_destroy_bucket" {
  description = "Let terraform destroy delete the S3 bucket even when it holds uploaded media and documents."
  type        = bool
  default     = false
}

variable "wait_timeout_minutes" {
  description = "How long terraform apply waits for the API to answer /healthz after (re)creating the instance."
  type        = number
  default     = 20
}

variable "tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}

# ---------- AI assistant: the dezr-ai agent on Bedrock AgentCore ----------

variable "agent_runtime_id" {
  description = <<-EOT
    The dezr-ai agent's AgentCore runtime (in dezr-ai: `terraform output agent_runtime_id`).
    POST /api/v1/ai/messages[/stream] relay visitors' messages to it, and the instance role may
    invoke exactly this runtime. Null turns the assistant off (those endpoints answer 503).
  EOT
  type        = string
  default     = "dezr_ai_agent-Rchwub39xj"
}

variable "agent_runtime_region" {
  description = "Region of the AgentCore runtime; null uses aws_region."
  type        = string
  default     = null
}

variable "ai_require_api_key" {
  description = <<-EOT
    Require "Authorization: Bearer <key>" on the AI endpoints, so only dezr-ui's server can
    spend model tokens. The key is generated into SSM (output ai_api_key_parameter); give it to
    dezr-ui as NUXT_CHAT_API_TOKEN. Turn off only if browsers must call the API directly.
  EOT
  type        = bool
  default     = true
}

variable "ai_rate_limit_per_minute" {
  description = "Messages one visitor (IP) may send the assistant per minute; 0 for no limit."
  type        = number
  default     = 10
}

variable "ai_daily_message_limit" {
  description = "Messages all visitors together may send the assistant per 24 hours (a ceiling on model costs); 0 for no limit."
  type        = number
  default     = 1000
}

variable "ai_smoke_test" {
  description = "After a (re)deploy, ask the assistant one question through the API, so apply proves the API reaches the agent."
  type        = bool
  default     = true
}

variable "ai_smoke_test_prompt" {
  description = "The smoke test's question."
  type        = string
  default     = "What does the firm do?"
}
