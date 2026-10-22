output "api_url" {
  description = "Base URL of the API. Use it as NUXT_CONTENT_API in dezr-ui."
  value       = "http://${aws_eip.api.public_ip}"
  # Only reported once the API answers.
  depends_on = [terraform_data.wait_for_api]
}

output "document_search_url" {
  description = "dezr-ai's retrieval endpoint (POST, Authorization: Bearer <document_search_key_parameter's value>)."
  value       = "http://${aws_eip.api.public_ip}/api/v1/documents/search"
}

output "document_search_key_parameter" {
  description = "SSM parameter holding the search key; dezr-ai's AgentCore runtime reads it."
  value       = aws_ssm_parameter.document_search_key.name
}

output "ai_chat_url" {
  description = "The assistant's chat (POST; /stream for server-sent events). Point dezr-ui's NUXT_CHAT_UPSTREAM at api_url."
  value       = local.agent_runtime_arn == null ? null : "http://${aws_eip.api.public_ip}/api/v1/ai/messages"
}

output "ai_api_key_parameter" {
  description = "SSM parameter with the chat's bearer key: set it as dezr-ui's NUXT_CHAT_API_TOKEN."
  value       = var.ai_require_api_key ? aws_ssm_parameter.ai_api_key[0].name : null
}

output "agent_runtime_arn" {
  description = "The dezr-ai AgentCore runtime the chat relays to."
  value       = local.agent_runtime_arn
}

output "embeddings" {
  description = "What documents are made searchable with."
  value = var.retrieval_backend == "bedrock_kb" ? "Bedrock Knowledge Base ${var.knowledge_base_id}" : (
    var.embedding_provider == "none" ? "keyword search only" : "${var.embedding_provider} ${local.embedding_model_id}"
  )
}

output "knowledge_base_s3_uri" {
  description = "With retrieval_backend = \"bedrock_kb\": the S3 location the knowledge base's data source must read."
  value       = local.knowledge_base ? local.knowledge_base_s3_uri : null
}

output "health_url" {
  value = local.health_url
}

output "swagger_url" {
  description = "Interactive API docs."
  value       = "http://${aws_eip.api.public_ip}/swagger-ui"
}

output "public_ip" {
  description = "Elastic IP of the instance (stays the same across redeploys)."
  value       = aws_eip.api.public_ip
}

output "instance_id" {
  value = aws_instance.api.id
}

output "shell" {
  description = "Open a shell on the instance (needs the Session Manager plugin for the AWS CLI)."
  value       = "aws ssm start-session --region ${var.aws_region} --target ${aws_instance.api.id}"
}

output "ssh_command" {
  description = "SSH in as ec2-user (only when SSH is on: ssh_public_key_path or ssh_key_name)."
  value = !local.ssh_enabled ? null : (
    local.ssh_identity != null
    ? "ssh -i ${local.ssh_identity} ec2-user@${aws_eip.api.public_ip}"
    : "ssh ec2-user@${aws_eip.api.public_ip}"
  )
}

output "deploy_log" {
  description = "On the instance: follow the deployment."
  value       = "sudo tail -f /var/log/dezr-deploy.log"
}

output "bucket" {
  description = "S3 bucket with uploaded media and documents (under dezr/) and the app bundle (deploy/)."
  value       = aws_s3_bucket.main.id
}

output "admin_email" {
  description = "The default admin's sign-in."
  value       = var.admin_email
}

output "admin_password" {
  description = "The default admin's password: terraform output -raw admin_password"
  value       = local.admin_password
  sensitive   = true
}
