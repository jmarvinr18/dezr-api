# ---------- Smoke test: the API reaches the dezr-ai agent ----------
# Once the API answers /healthz after a (re)deploy, ask the assistant one question through
# POST /api/v1/ai/messages. That covers the whole path: this API → AgentCore (the instance role's
# bedrock-agentcore:InvokeAgentRuntime) → dezr-ai → its document search back on this API.
# The key travels in an environment variable, never on the command line.

locals {
  ai_smoke_test = var.ai_smoke_test && local.agent_runtime_arn != null
  ai_chat_url   = "http://${aws_eip.api.public_ip}/api/v1/ai/messages"

  ai_smoke_sh = <<-EOT
    out=$(mktemp)
    echo "Asking the assistant through $AI_URL ..."
    code=$(curl -sS -o "$out" -w '%%{http_code}' --max-time 180 -X POST "$AI_URL" \
      -H 'Content-Type: application/json' -H "Authorization: Bearer $AI_API_KEY" --data "$AI_BODY")
    if [ "$code" != "201" ]; then
      echo "The assistant did not answer (HTTP $code): $(head -c 500 "$out")" >&2
      rm -f "$out"
      exit 1
    fi
    echo "The assistant answered: $(head -c 300 "$out")"
    rm -f "$out"
  EOT

  ai_smoke_ps = <<-EOT
    Write-Host "Asking the assistant through $env:AI_URL ..."
    try {
      $response = Invoke-RestMethod -Method Post -Uri $env:AI_URL -TimeoutSec 180 `
        -Headers @{ Authorization = "Bearer $env:AI_API_KEY" } -ContentType 'application/json; charset=utf-8' `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($env:AI_BODY))
      $answer = [string]$response.assistantMessage.content
      if ($answer.Length -gt 300) { $answer = $answer.Substring(0, 300) + '...' }
      Write-Host "The assistant answered: $answer"
    } catch {
      Write-Error "The assistant did not answer: $($_.Exception.Message) $($_.ErrorDetails.Message)"
      exit 1
    }
  EOT
}

resource "terraform_data" "ai_smoke_test" {
  count = local.ai_smoke_test ? 1 : 0

  triggers_replace = [terraform_data.wait_for_api.id, local.agent_runtime_arn]

  provisioner "local-exec" {
    interpreter = local.on_windows ? ["PowerShell", "-NoProfile", "-NonInteractive", "-Command"] : ["/bin/sh", "-c"]
    command     = local.on_windows ? local.ai_smoke_ps : local.ai_smoke_sh
    environment = {
      AI_URL     = local.ai_chat_url
      AI_BODY    = jsonencode({ content = var.ai_smoke_test_prompt })
      AI_API_KEY = var.ai_require_api_key ? random_password.ai_api_key[0].result : ""
    }
  }
}
