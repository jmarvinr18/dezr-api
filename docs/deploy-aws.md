# Deploying dezr-api to AWS at minimal cost

A plan for giving dezr-api a public HTTPS address for the dezr-ui site on AWS Amplify, at
roughly $0–1 a month while traffic is low. Nothing here has been created yet.

dezr-ui's server is the only caller: set its `NUXT_CONTENT_API` (in Amplify) to the address this
produces, then rebuild the site.

## Why this setup

- **App Runner** is closed to new customers since April 30, 2026.
- **ECS Express Mode**, AWS's suggested replacement, works with the existing image, but its
  Application Load Balancer alone costs about $16–20 a month, before compute and the database.
- **RDS** has no lasting free option; the smallest instance is roughly $15–20 a month.
- **AWS Lambda** has a monthly free allowance and a free public HTTPS address (Function URL), and
  can run the existing Docker image unchanged through the AWS Lambda Web Adapter.

Prices are approximate; check current AWS and Neon pricing before committing.

## The setup

| Piece | Service | Approx. monthly cost |
| --- | --- | --- |
| API | The dezr-api Docker image on **AWS Lambda**, reached through a **Lambda Function URL** | $0 within Lambda's free allowance (1M requests a month) |
| Database | **Neon** free plan: hosted Postgres with pgvector, in the AWS Singapore region | $0 (small storage limit, pauses when idle) |
| Files | Private **S3** bucket, encrypted (SSE-S3) | a few cents |
| Image | **ECR** repository | a few cents |
| Document processing | A second Lambda from the same image, run every 5 minutes by **EventBridge Scheduler** | $0 |
| Logs | CloudWatch Logs | $0 within the free allowance |
| Embeddings | `EMBEDDING_PROVIDER=none` (keyword search) at first; Bedrock later | $0, later fractions of a cent per document |

Secrets go in the Lambda's environment variables (encrypted at rest, free) rather than Secrets
Manager.

## Changes dezr-api needs first

1. **Upload size.** Lambda accepts requests up to 6 MB, so the current 10 MB media and 25 MB
   document limits won't fit. Simplest fix: cap uploads at 5 MB here (`MAX_CONTENT_LENGTH` and the
   upload validators) and in dezr-ui (`MAX_UPLOAD_BYTES`, `MAX_DOCUMENT_BYTES` and the admin pages'
   messages). To remove the cap later, switch to presigned uploads that go straight from the
   browser to S3.
2. **Startup.** Migrations and page seeding run on every container start
   (`docker-entrypoint.sh`). On Lambda that would run on every cold start, so set
   `RUN_MIGRATIONS=0` and `SEED_CONTENT=0`, and run `flask db upgrade` and `flask seed-content`
   once from a workstation instead.
3. **Web server.** Add the AWS Lambda Web Adapter to the Dockerfile (one `COPY` of the adapter
   extension), listening on port 8000. Use one gunicorn worker, since a Lambda instance handles one
   request at a time.
4. **Document processing.** Background threads stop when Lambda pauses after a response, so set
   `INGESTION_MODE=worker` and add a small Lambda entry point (with `awslambdaric` in the image)
   that runs what `flask documents process-pending` runs.

## Setup steps

1. **Neon:** create a free project in the AWS Singapore region, run `CREATE EXTENSION vector;`,
   and copy the connection string (with `sslmode=require`).
2. **From a workstation**, with `DATABASE_URL` pointing at Neon:
   ```bash
   uv run flask db upgrade
   uv run flask seed-content
   uv run flask admin create you@example.com
   ```
3. **S3:** create a private bucket in `ap-southeast-1` with Block Public Access on and default
   encryption on.
4. **ECR:** create a repository, then build and push the image.
5. **IAM role for both Lambdas:** read and write on that bucket (the `S3_KEY_PREFIX` prefix,
   `dezr/` by default) and CloudWatch Logs. Add `bedrock:InvokeModel` only when embeddings are
   turned on.
6. **API Lambda:** from the image, 1 GB memory, 30-second timeout, a Function URL (auth type NONE;
   the API checks admin tokens itself). Environment variables:

   | Variable | Value |
   | --- | --- |
   | `DATABASE_URL` | the Neon connection string |
   | `SECRET_KEY` | random, 32+ characters |
   | `STORAGE_BACKEND` | `s3` |
   | `S3_BUCKET` | the bucket name |
   | `S3_REGION` | `ap-southeast-1` |
   | `INGESTION_MODE` | `worker` |
   | `EMBEDDING_PROVIDER` | `none` (later `bedrock`, with `EMBEDDING_REGION`) |
   | `RUN_MIGRATIONS` | `0` |
   | `SEED_CONTENT` | `0` |
   | `TRUSTED_PROXY_COUNT` | see below |

7. **Processing Lambda:** same image and role, the process-pending entry point, 5-minute timeout,
   and an EventBridge Scheduler schedule `rate(5 minutes)`.
8. **Check:** `https://<function-url>/healthz` answers `{"status": "ok"}`.
9. **Amplify (dezr-ui):** set `NUXT_CONTENT_API=https://<function-url>` (origin only, no trailing
   slash), then rebuild `main`. dezr-ui reads its settings at build time.

### TRUSTED_PROXY_COUNT

Look at one real request's `X-Forwarded-For` in the Lambda log. Count the proxy addresses after
the visitor's own (Amazon's CDN in front of Amplify, the dezr-ui server, the Function URL) and set
that number. Never set it higher than the real count, or a caller can fake their address and get
around the sign-in lockout and the contact-form limit.

## Trade-offs

- **Cold starts:** after a quiet period, the first request can take a few seconds while Lambda
  starts Python and Neon wakes up. dezr-ui waits up to 15 seconds per request.
- **5 MB upload cap** until presigned S3 uploads are added.
- **Neon's free plan** has storage and compute limits; check them against expected content.

### Alternatives without code changes

- **Lightsail container service** (Nano): about $7 a month, HTTPS address included, no 6 MB limit.
- **Small EC2 instance** with the Docker image: about $6 a month, plus about $3.60 for its public
  IPv4 address; HTTPS needs a reverse proxy such as Caddy.
- **ECS Express Mode:** managed, with load balancer and HTTPS address created for you, from about
  $30 a month because of the load balancer.

## Not covered

The AI assistant's backend (vendor-portal-api) is a separate deployment and can follow the same
Lambda pattern later. dezr-ui's `NUXT_CHAT_UPSTREAM` stays unset until then.

## References

- [What is AWS App Runner (AWS docs)](https://docs.aws.amazon.com/apprunner/latest/dg/what-is-apprunner.md)
- [Encore: The end of App Runner](https://encore.dev/articles/end-of-app-runner)
- [Resources created by Amazon ECS Express Mode services](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-work.html)
- [Create your first Express Mode service using the AWS CLI](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-getting-started.html)
