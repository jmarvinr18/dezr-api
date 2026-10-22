# dezr API on one EC2 instance

`terraform apply` creates one small EC2 instance running the stack from `../docker-compose.yml`:
PostgreSQL with pgvector, the API on port 80, and the document worker. Apply returns once the API
answers `GET /healthz`, so it is usable straight away at the `api_url` output.

```
Internet ──:80──▶ Elastic IP ──▶ EC2 t4g.small (Amazon Linux 2023, Docker)
                                   ├─ api      dezr-api, gunicorn, port 80
                                   ├─ worker   flask documents worker (RAG ingestion)
                                   └─ db       pgvector/pgvector:pg17, 127.0.0.1 only
                                        └─ data on its own EBS volume (kept across redeploys)
                         S3 bucket: dezr/  uploaded media and documents
                                    deploy/ the app bundle the instance builds from
                         SSM Parameter Store: generated DB password, SECRET_KEY, admin password
```

## What it creates

| Resource | Why |
| --- | --- |
| VPC, public subnet, internet gateway, route table | A network of its own (no NAT gateway, so nothing to pay for) |
| Security group | Port 80 from `allowed_cidrs`; port 22 only with SSH on (`ssh_public_key_path`), and only from your own IP. PostgreSQL is never exposed. |
| EC2 instance (`t4g.small`, Graviton) | Docker host. Builds the API image on first boot from the uploaded bundle. |
| EBS volume (gp3, 10 GiB, encrypted) | PostgreSQL's data, on its own disk so redeploys keep the database |
| Elastic IP | A fixed address across redeploys |
| S3 bucket (private, encrypted) | Media and document uploads (`STORAGE_BACKEND=s3`) and the app bundle. Admin > Documents uploads straight to it from the browser (`upload_origins`), since Amplify Hosting refuses request bodies over about 4.5 MB |
| IAM role + instance profile | S3 access to that bucket, its SSM parameters, Session Manager, invoking the dezr-ai AgentCore runtime, and Bedrock embeddings or the knowledge base when enabled. No access keys anywhere. |
| SSM parameters (SecureString) | Generated PostgreSQL password, `SECRET_KEY`, the default admin's password, the document search key dezr-ai uses (`/dezr-api/document-search-key`), and the AI chat key dezr-ui sends (`/dezr-api/ai-api-key`). Kept out of user data. |

### Why `t4g.small`

It is the cheapest instance that runs all three containers comfortably: 2 vCPU (Graviton) and
2 GiB of RAM, plus 2 GiB of swap the deploy script adds. That leaves room to build the image on
boot and to parse large PDFs. Every dependency has an ARM64 wheel, and the image was built and
checked for `linux/arm64`. `t4g.micro` (1 GiB) halves the instance cost but swaps while
processing documents. x86 types such as `t3a.small` also work: the AMI follows the instance
type's architecture.

Rough monthly cost, on-demand, always on (check current AWS prices):

| | ap-southeast-1 | us-east-1 |
| --- | --- | --- |
| t4g.small | ~$15.5 | ~$12.3 |
| 30 GiB gp3 (root 20 + data 10) | ~$2.9 | ~$2.4 |
| Public IPv4 (Elastic IP) | ~$3.7 | ~$3.7 |
| S3, SSM, data transfer at low volume | < $1 | < $1 |
| **Total** | **~$22** | **~$18** |

## Use

Prerequisites: Terraform 1.5+, AWS credentials (`aws configure` / `aws sso login` / environment
variables) allowed to create the resources above, and the Session Manager plugin if you want a
shell on the instance.

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # optional; every setting has a default
terraform init
terraform apply
```

Apply takes about 6–10 minutes. Most of that is the instance installing Docker and building the
image, and apply waits for it. Then:

```bash
terraform output api_url                 # http://<elastic-ip>
curl "$(terraform output -raw api_url)/healthz"
terraform output admin_email             # admin@deszr.com, the default admin
terraform output -raw admin_password     # its password
```

Swagger UI is at `terraform output swagger_url`.

### Default admin

The first boot creates **`admin@deszr.com`** (variable `admin_email`) with a generated password,
or with `admin_password` if you set one (12-200 characters). Sign in to dezr-ui's `/admin` with
them. It is created only while the database has no admin, so redeploys never reset it. Change the
password after the first sign-in (`flask admin set-password admin@deszr.com` on the instance).
Set `admin_email = null` to skip it.

### Point dezr-ui at it

Set `NUXT_CONTENT_API` to the `api_url` output. Once you know the address of the server dezr-ui
runs on, narrow `allowed_cidrs` to it (and your own IP) and set `trusted_proxy_count = 1`. The
API then takes visitors' IPs from the `X-Forwarded-For` header dezr-ui sends, so sign-in lockouts
and contact-form limits count visitors, not the dezr-ui server.

### The AI assistant (dezr-ai)

The API relays the chat to the dezr-ai agent's AgentCore runtime, `agent_runtime_id`
(default `dezr_ai_agent-oWJlg6Fpai`, dezr-ai's `terraform output agent_runtime_id`, in the same
account and in `agent_runtime_region`, default `aws_region`). Apply gives the instance role
`bedrock-agentcore:InvokeAgentRuntime` on exactly that runtime, and then asks the assistant one
question through the API (`ai_smoke_test`), so a finished apply means the API reaches the agent.

The chat endpoints require a bearer key (`ai_require_api_key`, default on), so only dezr-ui's
server can spend model tokens. Point dezr-ui's chat at this API:

```bash
NUXT_CHAT_UPSTREAM=$(terraform output -raw api_url)
NUXT_CHAT_API_TOKEN=$(aws ssm get-parameter --region ap-southeast-1 --with-decryption \
  --name "$(terraform output -raw ai_api_key_parameter)" --query Parameter.Value --output text)
```

dezr-ui's chat proxy should also forward `X-Forwarded-For`, so each visitor gets their own rate
limit (`ai_rate_limit_per_minute`, default 10); everyone together is capped by
`ai_daily_message_limit` (1000 per 24 hours). Set `agent_runtime_id = null` to turn the
assistant off.

## Redeploying

The instance builds whatever source the bundle holds. The bundle is assembled from an explicit
list of files (`app/**/*.py`, the seed pages, migrations, `pyproject.toml`, `uv.lock`,
`Dockerfile`, compose file, entrypoint), so `app/.env`, `.venv` and tests never leave your machine.

- **Code or settings changed:** `terraform apply` uploads a new bundle and **replaces the
  instance**. The old one is shut down cleanly first, and the new one builds and starts the stack
  on the same Elastic IP. Expect about 5–8 minutes of downtime. The database (its own EBS volume)
  and uploads (S3) carry over.
- **Restart in place** (e.g. after editing `/opt/dezr/deploy.env` on the instance):
  `sudo /opt/dezr/deploy.sh`.
- A newer Amazon Linux image alone does not replace the instance (`ignore_changes = [ami]`). Use
  `terraform apply -replace=aws_instance.api` to move to it.

## Operating it

### SSH

SSH is off unless you turn it on. In `terraform.tfvars` (git-ignored):

```hcl
ssh_public_key_path = "~/.ssh/dezr-api-debug.pub"   # your public key; the private half stays with you
```

Make a key for it first if you don't have one: `ssh-keygen -t ed25519 -f ~/.ssh/dezr-api-debug`.
Apply registers the key as an EC2 key pair and opens port 22 **only to the public IP running
terraform apply** (`ssh_allow_my_ip`, looked up at checkip.amazonaws.com on every run). If your
IP changes, run `terraform apply` again and only the firewall rule changes. Add colleagues' IPs
with `ssh_cidrs`. EC2 installs the key at launch only, so turning SSH on, or changing the key,
replaces the instance (the usual redeploy: the database and uploads carry over).

```bash
$(terraform output -raw ssh_command)                  # ssh -i ~/.ssh/dezr-api-debug ec2-user@<ip>
```

`ec2-user` is in the `docker` group, so `docker compose` works without `sudo`. Remove the line
from `terraform.tfvars` and apply to close port 22 again. Session Manager (below) needs no open
port and works either way.

### Commands

```bash
$(terraform output -raw shell)                        # shell via Session Manager
sudo tail -f /var/log/dezr-deploy.log                 # first-boot deployment
cd /opt/dezr/app && sudo docker compose ps            # containers
sudo docker compose logs -f api worker
sudo docker compose exec api flask admin create you@example.com
sudo docker compose exec api flask documents list
sudo docker compose exec db psql -U dezr dezr
```

The containers restart on their own after a reboot (`restart: unless-stopped`).

**Backups:** take EBS snapshots of the `dezr-api-postgres` volume (AWS Backup or Data Lifecycle
Manager), or `pg_dump` through `docker compose exec db`. `terraform destroy` snapshots the volume
first while `snapshot_data_on_destroy = true` (default).

## Document embeddings

`embedding_provider` picks the model that embeds uploaded documents' passages, and you can change
it whenever you like:

| `embedding_provider` | `embedding_model_id` (null: the first) | Needs |
| --- | --- | --- |
| `"openai"` (default) | `text-embedding-3-small`, `text-embedding-3-large` | `openai_api_key` |
| `"bedrock"` | `cohere.embed-multilingual-v3`, `amazon.titan-embed-text-v2:0` | Cohere: the Marketplace subscription below. Titan: an `embedding_region` that has it (not ap-southeast-1). The role gets `bedrock:InvokeModel` on the model only |
| `"none"` | | Nothing: keyword search only |

Give the OpenAI key without writing it to a file; Terraform stores it as the SSM SecureString
`/<name>/openai-api-key`, and a new key redeploys:

```bash
TF_VAR_openai_api_key=sk-... terraform apply
```

```powershell
$env:TF_VAR_openai_api_key = "sk-..."; terraform apply
```

**Switching** (provider, model, or the knowledge base below) is a `terraform apply`: the instance
is rebuilt with the new settings, and the document worker embeds the existing documents again
when it starts, along with documents that failed because the old model was unavailable. Search
finds a document by keywords until its turn comes. `terraform output embeddings` shows what is
in use, and on the instance:

```bash
cd /opt/dezr/app && sudo docker compose exec api flask documents index
```

### A Bedrock Knowledge Base instead

`retrieval_backend = "bedrock_kb"` hands chunking, embedding and search to an existing Bedrock
Knowledge Base (make it in the console or with its own IaC; its embedding model is its own
choice). Give it an **S3 data source** reading `terraform output knowledge_base_s3_uri`, which is
this deployment's bucket under `knowledge-base/` unless you set `knowledge_base_s3_uri`, then:

```hcl
retrieval_backend             = "bedrock_kb"
knowledge_base_id             = "ABCDEFGHIJ"
knowledge_base_data_source_id = "KLMNOPQRST"
```

Each upload is copied there as `<document id>.<ext>` and indexed right away (no full sync), with
the document's id and title as metadata; deleting a document removes it. dezr-ai keeps calling
the API's search endpoint, which now asks the knowledge base. The role gets `s3:PutObject` /
`s3:DeleteObject` on that prefix and `bedrock:IngestKnowledgeBaseDocuments`,
`GetKnowledgeBaseDocuments`, `DeleteKnowledgeBaseDocuments` and `Retrieve` on the knowledge base.
The knowledge base's own service role needs to read the bucket. Switching back to `"pgvector"`
embeds the documents locally again.

**Cohere's first use (once per AWS account).** Cohere is a third-party model sold through AWS
Marketplace. Its first call subscribes the account and accepts Cohere's EULA, which needs
`aws-marketplace:Subscribe`; the instance role deliberately doesn't have it, so until someone
subscribes, every document fails with "The embedding service is unavailable". Before the first
upload, call it once as an admin (or open it in the Bedrock console's playground) and wait for the
availability to show `AVAILABLE` (up to 15 minutes):

```bash
aws bedrock-runtime invoke-model --region ap-southeast-1 --model-id cohere.embed-multilingual-v3 --cli-binary-format raw-in-base64-out --body '{"texts":["hello"],"input_type":"search_document"}' cohere-check.json
```

```bash
aws bedrock get-foundation-model-availability --region ap-southeast-1 --model-id cohere.embed-multilingual-v3
```

To process documents again by hand (`--stale`: indexed with another model or backend; `--failed`;
`--all`, e.g. after changing the chunking):

```bash
cd /opt/dezr/app && sudo docker compose exec api flask documents reprocess --stale
```

## Security notes

- **The API is plain HTTP.** Admin sign-in and tokens, and dezr-ai's document search key, cross
  the network unencrypted. Before real use, restrict `allowed_cidrs` to the dezr-ui server, or put TLS in front: an Application Load
  Balancer with an ACM certificate, or a reverse proxy such as Caddy on the instance with a domain
  name.
- PostgreSQL is bound to the instance's loopback interface; the security group only opens port 80
  (and 22 if you ask for SSH).
- IMDSv2 is required. The hop limit is 2 so the containers can use the instance role.
- The generated secrets are also in the Terraform state. Keep the state private (an encrypted S3
  backend is sketched in `versions.tf`).

## Destroying

`terraform destroy` removes everything. The database volume is snapshotted first (unless you
turned that off). The bucket is only deleted when it is empty, unless `force_destroy_bucket = true`.

## Troubleshooting

- **Apply times out waiting for `/healthz`:** open a shell (`terraform output -raw shell`) and
  read `/var/log/dezr-deploy.log`, then `/var/log/cloud-init-output.log`. Fix the cause, then
  `sudo /opt/dezr/deploy.sh` or `terraform apply` again: the wait step re-runs on its own.
- **The boot log (`aws ec2 get-console-output --latest`) says `deploy.sh: cannot execute:
  required file not found`:** the script had Windows (CRLF) line endings. Terraform now converts
  the user data and the app bundle to LF itself, and `tests/test_line_endings.py` fails on CRLF
  in the shell scripts, Dockerfile, compose file and templates.
- **`Instance type … is not offered`:** pick another `instance_type` or `aws_region`.
- **Documents stay `failed` with "The embedding service is unavailable":** `docker compose logs
  worker` says why. OpenAI: a wrong key (401) or no credit (429 `insufficient_quota`). Bedrock: the
  model isn't enabled in `embedding_region`, or Cohere's Marketplace subscription is missing.
  Fix it, then `flask documents reprocess --failed` (or switch models: those are retried then).
- **Documents fail with "The knowledge base couldn't index the document":** the reason that
  follows is the knowledge base's own (unsupported content, a file too large for its parser, ...).
  "The knowledge base is unavailable": check the role's permissions and the knowledge base id.

## Files

| File | |
| --- | --- |
| `versions.tf` | Terraform and provider versions, AWS provider with default tags |
| `variables.tf` | Settings (all have defaults) |
| `network.tf` | VPC, subnet, routing, security group |
| `iam.tf` | Instance role and its permissions |
| `storage.tf` | S3 bucket (with the CORS rule for direct document uploads and a lifecycle rule that expires unconfirmed ones), the app bundle, generated secrets in SSM |
| `compute.tf` | AMI, instance, data volume, Elastic IP, and the wait for `/healthz` |
| `assistant.tf` | The smoke test: one question to the assistant after a (re)deploy |
| `ssh.tf` | Optional SSH: your public key as an EC2 key pair, port 22 only from your own IP |
| `outputs.tf` | URL, IP, shell command, admin password (sensitive) |
| `templates/user_data.sh.tftpl` | First boot: writes the settings and runs the deploy script |
| `scripts/deploy.sh` | Swap, Docker + Compose + Buildx, data volume, bundle, secrets (incl. the default admin), `docker compose up` |
