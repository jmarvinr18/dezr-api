# The instance's role. The containers get AWS credentials from it through the instance
# metadata service (no access keys anywhere).

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  media_prefix     = "dezr" # S3_KEY_PREFIX: media/ and documents/ live under it
  embedding_region = coalesce(var.embedding_region, var.aws_region)
  agent_region     = coalesce(var.agent_runtime_region, var.aws_region)
  agent_runtime_arn = var.agent_runtime_id == null ? null : join(":", [
    "arn", data.aws_partition.current.partition, "bedrock-agentcore", local.agent_region,
    data.aws_caller_identity.current.account_id, "runtime/${var.agent_runtime_id}",
  ])
}

data "aws_iam_policy_document" "assume_ec2" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api" {
  name_prefix        = "${var.name}-"
  assume_role_policy = data.aws_iam_policy_document.assume_ec2.json
}

# Session Manager: a shell on the instance without SSH or an open port.
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.api.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "ReadAppBundle"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.main.arn}/deploy/*"]
  }

  statement {
    sid       = "MediaAndDocuments"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.main.arn}/${local.media_prefix}/*"]
  }

  # Lets a missing file read as 404 rather than 403.
  statement {
    sid       = "ListMediaAndDocuments"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.main.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${local.media_prefix}/*"]
    }
  }

  statement {
    sid     = "ReadSecrets"
    actions = ["ssm:GetParameter"]
    resources = [
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/${var.name}/*",
    ]
  }

  dynamic "statement" {
    for_each = var.embedding_provider == "bedrock" ? [1] : []
    content {
      sid     = "EmbedDocuments"
      actions = ["bedrock:InvokeModel"]
      resources = [
        "arn:${data.aws_partition.current.partition}:bedrock:${local.embedding_region}::foundation-model/${var.embedding_model_id}",
      ]
    }
  }

  # The AI chat endpoints relay to the dezr-ai agent (IAM-authorised AgentCore runtime).
  dynamic "statement" {
    for_each = local.agent_runtime_arn == null ? [] : [1]
    content {
      sid       = "InvokeAgent"
      actions   = ["bedrock-agentcore:InvokeAgentRuntime"]
      resources = [local.agent_runtime_arn, "${local.agent_runtime_arn}/*"]
    }
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "${var.name}-app"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

resource "aws_iam_instance_profile" "api" {
  name_prefix = "${var.name}-"
  role        = aws_iam_role.api.name
}
