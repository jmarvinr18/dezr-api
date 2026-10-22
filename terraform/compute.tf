# One EC2 instance running the stack from docker-compose.yml: PostgreSQL with pgvector, the API
# on port 80, and the document worker. The database lives on its own EBS volume and uploads in
# S3, so the instance can be replaced (it is, on every code change) without losing data.

data "aws_ec2_instance_type" "selected" {
  instance_type = var.instance_type
}

locals {
  architecture = contains(data.aws_ec2_instance_type.selected.supported_architectures, "arm64") ? "arm64" : "x86_64"
}

# Latest Amazon Linux 2023 for the instance's architecture.
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-${local.architecture}"
}

resource "aws_eip" "api" {
  domain = "vpc"

  tags = { Name = var.name }

  depends_on = [aws_internet_gateway.main]
}

resource "aws_ebs_volume" "data" {
  availability_zone = local.availability_zone
  size              = var.data_volume_size
  type              = "gp3"
  encrypted         = true
  final_snapshot    = var.snapshot_data_on_destroy

  tags = { Name = "${var.name}-postgres" }
}

locals {
  # Linux line endings throughout, whatever the checkout has: on a Windows checkout the deploy
  # script's first line becomes "#!/bin/bash\r", which can't run ("required file not found"),
  # and the instance never deploys.
  user_data = replace(templatefile("${path.module}/templates/user_data.sh.tftpl", {
    config = {
      AWS_REGION          = var.aws_region
      BUCKET              = aws_s3_bucket.main.id
      BUNDLE_KEY          = aws_s3_object.bundle.key
      PARAM_PREFIX        = "/${var.name}"
      PUBLIC_IP           = aws_eip.api.public_ip
      DATA_VOLUME_ID      = aws_ebs_volume.data.id
      CORS_ORIGINS        = join(",", var.cors_origins)
      TRUSTED_PROXY_COUNT = tostring(var.trusted_proxy_count)
      MEDIA_PREFIX        = local.media_prefix
      EMBEDDING_PROVIDER  = var.embedding_provider
      EMBEDDING_MODEL_ID  = var.embedding_model_id == null ? "" : var.embedding_model_id
      EMBEDDING_REGION    = local.embedding_region
      # A new key (a new parameter version) redeploys, since deploy.sh reads it at boot.
      OPENAI_KEY_VERSION            = length(aws_ssm_parameter.openai_api_key) > 0 ? tostring(aws_ssm_parameter.openai_api_key[0].version) : ""
      RETRIEVAL_BACKEND             = var.retrieval_backend
      KNOWLEDGE_BASE_ID             = local.knowledge_base ? var.knowledge_base_id : ""
      KNOWLEDGE_BASE_DATA_SOURCE_ID = local.knowledge_base ? var.knowledge_base_data_source_id : ""
      KNOWLEDGE_BASE_S3_URI         = local.knowledge_base ? local.knowledge_base_s3_uri : ""
      KNOWLEDGE_BASE_REGION         = local.knowledge_base_region
      KNOWLEDGE_BASE_SEARCH_TYPE    = var.knowledge_base_search_type == null ? "" : var.knowledge_base_search_type
      ADMIN_EMAIL                   = var.admin_email == null ? "" : var.admin_email
      AI_AGENT_RUNTIME_ARN          = local.agent_runtime_arn == null ? "" : local.agent_runtime_arn
      AI_AGENT_REGION               = local.agent_region
      AI_REQUIRE_KEY                = var.ai_require_api_key ? "1" : "0"
      AI_RATE_LIMIT_PER_MINUTE      = tostring(var.ai_rate_limit_per_minute)
      AI_DAILY_MESSAGE_LIMIT        = tostring(var.ai_daily_message_limit)
      COMPOSE_VERSION               = "v2.29.7"
      BUILDX_VERSION                = "v0.17.1"
    }
    deploy_script = file("${path.module}/scripts/deploy.sh")
  }), "\r\n", "\n")
}

resource "aws_instance" "api" {
  ami                    = nonsensitive(data.aws_ssm_parameter.al2023.value)
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.api.id]
  iam_instance_profile   = aws_iam_instance_profile.api.name
  key_name               = local.ssh_key_name

  user_data = local.user_data
  # New code, settings or deploy script → a fresh instance built from them.
  user_data_replace_on_change = true

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
    # 2 hops, so containers (one network hop further) can get the role's credentials.
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_size
    encrypted             = true
    delete_on_termination = true
  }

  tags = { Name = var.name }

  lifecycle {
    # A newer Amazon Linux image alone shouldn't replace a running server.
    ignore_changes = [ami]

    precondition {
      condition     = var.embedding_provider != "openai" || var.openai_api_key != null
      error_message = "embedding_provider = \"openai\" needs openai_api_key (e.g. TF_VAR_openai_api_key=sk-... terraform apply), or choose another embedding_provider."
    }
    precondition {
      condition     = !local.knowledge_base || (var.knowledge_base_id != null && var.knowledge_base_data_source_id != null)
      error_message = "retrieval_backend = \"bedrock_kb\" needs knowledge_base_id and knowledge_base_data_source_id."
    }
  }

  depends_on = [
    aws_route_table_association.public,
    aws_iam_role_policy.api,
    aws_iam_role_policy_attachment.ssm_core,
    # deploy.sh reads these at boot
    aws_ssm_parameter.postgres_password,
    aws_ssm_parameter.secret_key,
    aws_ssm_parameter.document_search_key,
    aws_ssm_parameter.ai_api_key,
    aws_ssm_parameter.openai_api_key,
  ]
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.api.id
  # Shut the old instance down cleanly (PostgreSQL included) before the disk moves on.
  stop_instance_before_detaching = true
}

resource "aws_eip_association" "api" {
  instance_id   = aws_instance.api.id
  allocation_id = aws_eip.api.id
}

# ---------- Wait until the API answers ----------
# The instance installs Docker, builds the image and starts the stack on first boot (about 5
# minutes). terraform apply returns once GET /healthz answers 200, so the API is usable as soon
# as apply finishes. Runs again only when the instance is replaced.

locals {
  health_url = "http://${aws_eip.api.public_ip}/healthz"
  on_windows = substr(abspath(path.root), 1, 1) == ":"

  wait_sh = <<-EOT
    url='${local.health_url}'
    deadline=$(( $(date +%s) + ${var.wait_timeout_minutes} * 60 ))
    echo "Waiting for $url (the instance is installing Docker and building the API) ..."
    until curl -fsS --max-time 5 "$url" > /dev/null 2>&1; do
      if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "The API did not answer within ${var.wait_timeout_minutes} minutes. Check /var/log/dezr-deploy.log on the instance." >&2
        exit 1
      fi
      sleep 10
    done
    echo "API is up: $url"
  EOT

  wait_ps = <<-EOT
    $url = '${local.health_url}'
    $deadline = (Get-Date).AddMinutes(${var.wait_timeout_minutes})
    Write-Host "Waiting for $url (the instance is installing Docker and building the API) ..."
    while ($true) {
      try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -eq 200) { Write-Host "API is up: $url"; break }
      } catch {}
      if ((Get-Date) -gt $deadline) {
        Write-Error "The API did not answer within ${var.wait_timeout_minutes} minutes. Check /var/log/dezr-deploy.log on the instance."
        exit 1
      }
      Start-Sleep -Seconds 10
    }
  EOT
}

resource "terraform_data" "wait_for_api" {
  triggers_replace = [
    aws_instance.api.id,
    aws_eip_association.api.id,
    aws_volume_attachment.data.id,
  ]

  provisioner "local-exec" {
    interpreter = local.on_windows ? ["PowerShell", "-NoProfile", "-NonInteractive", "-Command"] : ["/bin/sh", "-c"]
    command     = local.on_windows ? local.wait_ps : local.wait_sh
  }
}
