import os
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent

load_dotenv(APP_DIR / ".env")


class Config:
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    PROPAGATE_EXCEPTIONS = True
    API_TITLE = "dezr API"
    API_VERSION = "v1"
    OPENAPI_VERSION = "3.0.3"
    OPENAPI_URL_PREFIX = ""
    OPENAPI_SWAGGER_UI_PATH = "/swagger-ui"
    OPENAPI_SWAGGER_UI_URL = "https://cdn.jsdelivr.net/npm/swagger-ui-dist/"
    API_SPEC_OPTIONS = {
        "components": {
            "securitySchemes": {
                "adminToken": {"type": "http", "scheme": "bearer", "description": "From POST /api/v1/admin/auth/login"},
                "searchKey": {"type": "http", "scheme": "bearer", "description": "DOCUMENT_SEARCH_API_KEY"},
                "aiKey": {"type": "http", "scheme": "bearer", "description": "AI_API_KEY (when set)"},
            }
        }
    }

    # Signs admin access tokens and keys the contact form's IP hashes. Required in production:
    # without it admin sign-in answers 503. In debug mode a random one is made per process.
    SECRET_KEY = os.getenv("SECRET_KEY")

    # ---- Admin sign-in ----
    # Hours an access token lasts before the admin has to sign in again.
    ADMIN_SESSION_HOURS = float(os.getenv("ADMIN_SESSION_HOURS", 8))
    # The admin `flask admin ensure-default` creates on first start, when there is no admin yet
    # (the Docker image runs it on start). An empty DEFAULT_ADMIN_EMAIL turns this off. Without
    # DEFAULT_ADMIN_PASSWORD a strong password is generated and printed once.
    DEFAULT_ADMIN_EMAIL = os.getenv("DEFAULT_ADMIN_EMAIL", "admin@deszr.com")
    DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", "")
    # Failed sign-ins allowed per IP and per account within the window, before a lockout.
    ADMIN_MAX_FAILURES = int(os.getenv("ADMIN_MAX_FAILURES", 5))
    ADMIN_LOCKOUT_MINUTES = int(os.getenv("ADMIN_LOCKOUT_MINUTES", 15))

    # Number of reverse proxies in front of the API that set X-Forwarded-For (the Nuxt server,
    # nginx, a load balancer). 0 trusts none, so the sign-in lockout and contact form limit
    # count per connecting address. Never set it higher than the real number of proxies, or
    # a caller can send a new X-Forwarded-For with every attempt.
    TRUSTED_PROXY_COUNT = int(os.getenv("TRUSTED_PROXY_COUNT", 0))

    # ---- Contact form ----
    # Enquiries one visitor (IP) may send per hour.
    CONTACT_RATE_LIMIT_PER_HOUR = int(os.getenv("CONTACT_RATE_LIMIT_PER_HOUR", 5))

    # ---- Media (images and PDFs uploaded in the admin area) ----
    # Where the files are stored: "s3" or "local" (development only).
    STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "s3")

    # S3 settings. Credentials are not configured here: boto3 uses the standard AWS chain
    # (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, AWS_PROFILE, or the instance/task role).
    S3_BUCKET = os.getenv("S3_BUCKET")
    S3_REGION = os.getenv("S3_REGION") or os.getenv("AWS_DEFAULT_REGION")
    # Only for S3-compatible services such as LocalStack or MinIO.
    S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL")
    S3_KEY_PREFIX = os.getenv("S3_KEY_PREFIX", "dezr")
    # "AES256" (S3-managed keys), "aws:kms", or empty to use the bucket's default.
    S3_SERVER_SIDE_ENCRYPTION = os.getenv("S3_SERVER_SIDE_ENCRYPTION", "AES256")
    S3_KMS_KEY_ID = os.getenv("S3_KMS_KEY_ID")

    # Used when STORAGE_BACKEND=local.
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", str(PROJECT_DIR / "uploads"))
    # One 10MB file plus form overhead.
    MAX_CONTENT_LENGTH = 11 * 1024 * 1024

    # Where the website serves uploaded files: media URLs in responses are <base>/<name>.
    # The default suits dezr-ui, which forwards /media/** to GET /api/v1/media/<name>.
    MEDIA_PUBLIC_BASE_URL = os.getenv("MEDIA_PUBLIC_BASE_URL", "/media").rstrip("/")

    # ---- Documents for the AI assistant (RAG ingestion) ----
    # Stored in the same place as media (STORAGE_BACKEND), under documents/.
    # "thread": processed in a background thread of the API process (development, small sites).
    # "worker": the API only stores uploads; run `flask documents worker` to process them.
    INGESTION_MODE = os.getenv("INGESTION_MODE", "thread")
    # How long a processor may hold a document before another one takes it over.
    INGESTION_LEASE_MINUTES = int(os.getenv("INGESTION_LEASE_MINUTES", 15))
    # Claims before a document that keeps crashing its processor is marked failed.
    INGESTION_MAX_ATTEMPTS = int(os.getenv("INGESTION_MAX_ATTEMPTS", 3))
    # Seconds `flask documents worker` waits between looks when nothing is waiting.
    INGESTION_POLL_SECONDS = float(os.getenv("INGESTION_POLL_SECONDS", 5))
    # Hybrid chunking: passages of up to CHUNK_MAX_TOKENS (approximate tokens); passages under
    # CHUNK_MIN_TOKENS are merged into a neighbouring section; prose cut mid-paragraph repeats
    # up to CHUNK_OVERLAP_TOKENS of the previous passage.
    CHUNK_MAX_TOKENS = int(os.getenv("CHUNK_MAX_TOKENS", 400))
    CHUNK_MIN_TOKENS = int(os.getenv("CHUNK_MIN_TOKENS", 60))
    CHUNK_OVERLAP_TOKENS = int(os.getenv("CHUNK_OVERLAP_TOKENS", 50))
    # Vectors for the passages: "bedrock" (Amazon Bedrock) or "none" (keyword search only).
    # Credentials come from the same AWS chain as S3; the identity needs bedrock:InvokeModel.
    EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "bedrock")
    # amazon.titan-embed-text-v2:0 or cohere.embed-multilingual-v3 (both 1024 dimensions).
    EMBEDDING_MODEL_ID = os.getenv("EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
    EMBEDDING_REGION = os.getenv("EMBEDDING_REGION") or os.getenv("AWS_DEFAULT_REGION")
    # Bearer key for POST /api/v1/documents/search (the AI assistant's retrieval). Unset turns
    # the endpoint off (503). Use 32+ random characters; dezr-ai's runtime reads the same value.
    DOCUMENT_SEARCH_API_KEY = os.getenv("DOCUMENT_SEARCH_API_KEY")

    # ---- AI assistant: POST /api/v1/ai/messages[/stream] relays to the dezr-ai agent ----
    # The agent is a separate service (dezr-ai: LangGraph on Amazon Bedrock AgentCore Runtime).
    # Set the runtime ARN to call AgentCore. Credentials come from the same AWS chain as S3;
    # the IAM identity needs bedrock-agentcore:InvokeAgentRuntime on it.
    AI_AGENT_RUNTIME_ARN = os.getenv("AI_AGENT_RUNTIME_ARN")
    AI_AGENT_REGION = os.getenv("AI_AGENT_REGION") or os.getenv("AWS_DEFAULT_REGION") or "ap-southeast-1"
    # The AgentCore endpoint to invoke: "DEFAULT", or an endpoint name.
    AI_AGENT_QUALIFIER = os.getenv("AI_AGENT_QUALIFIER", "DEFAULT")
    # Used instead when no runtime ARN is set: the agent over plain HTTP (dezr-ai's
    # `python main.py` locally: http://localhost:8080/invocations).
    AI_AGENT_URL = os.getenv("AI_AGENT_URL")
    # Sent as "Authorization: Bearer ..." with AI_AGENT_URL.
    AI_AGENT_TOKEN = os.getenv("AI_AGENT_TOKEN")
    # Longest wait for the agent between two pieces of its answer (it may search, grade and
    # rewrite the question before it writes anything).
    AI_AGENT_TIMEOUT_SECONDS = float(os.getenv("AI_AGENT_TIMEOUT_SECONDS", 120))
    # Bearer key for the AI endpoints. Set it when the API is reachable from the internet, so
    # only dezr-ui's server (NUXT_CHAT_API_TOKEN) can spend model tokens; unset leaves them open.
    AI_API_KEY = os.getenv("AI_API_KEY")
    # Stored messages sent to the agent as history, conversation length, messages one visitor
    # may send per minute, and messages everyone together may send per day (a cost ceiling).
    AI_HISTORY_MESSAGES = int(os.getenv("AI_HISTORY_MESSAGES", 20))
    AI_MAX_MESSAGES_PER_SESSION = int(os.getenv("AI_MAX_MESSAGES_PER_SESSION", 100))
    AI_RATE_LIMIT_PER_MINUTE = int(os.getenv("AI_RATE_LIMIT_PER_MINUTE", 10))
    AI_DAILY_MESSAGE_LIMIT = int(os.getenv("AI_DAILY_MESSAGE_LIMIT", 1000))

    # Comma-separated origins allowed to call the API (the Nuxt dev server by default).
    CORS_ORIGINS = [
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
        if o.strip()
    ]
