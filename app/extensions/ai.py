import logging

from flask import Flask, current_app

from app.database import db
from app.services.ai.agent_client import AgentClient, AgentCoreClient, HttpAgentClient, UnconfiguredAgent
from app.services.ai.chat_service import ChatLimits, ChatService

log = logging.getLogger(__name__)

_AGENT_KEY = "ai_agent_client"
NOT_CONFIGURED = "The AI assistant isn't configured on this server."


def _build_agent_client(config) -> AgentClient:
    """AgentCore when AI_AGENT_RUNTIME_ARN is set (dezr-ai deployed), otherwise a plain HTTP
    endpoint (AI_AGENT_URL, dezr-ai running locally)."""
    if config.get("AI_AGENT_RUNTIME_ARN"):
        import boto3
        from botocore.config import Config as BotoConfig

        # Credentials come from the standard AWS chain (env vars, profile, or the instance role).
        client = boto3.client(
            "bedrock-agentcore",
            region_name=config["AI_AGENT_REGION"],
            config=BotoConfig(
                retries={"max_attempts": 2, "mode": "standard"},
                connect_timeout=5,
                read_timeout=config["AI_AGENT_TIMEOUT_SECONDS"],
            ),
        )
        return AgentCoreClient(
            client, config["AI_AGENT_RUNTIME_ARN"], qualifier=config.get("AI_AGENT_QUALIFIER") or "DEFAULT"
        )
    if config.get("AI_AGENT_URL"):
        return HttpAgentClient(
            config["AI_AGENT_URL"], timeout=config["AI_AGENT_TIMEOUT_SECONDS"], token=config.get("AI_AGENT_TOKEN") or None
        )
    return UnconfiguredAgent(NOT_CONFIGURED)


def init_ai(app: Flask, agent_client: AgentClient | None = None) -> None:
    """Creates the agent client once per app. Pass one to override it (tests). If it can't be
    created, the AI endpoints answer 503 instead of the app failing to start."""
    if agent_client is not None:
        app.extensions[_AGENT_KEY] = agent_client
        return
    try:
        app.extensions[_AGENT_KEY] = _build_agent_client(app.config)
    except Exception as exc:  # missing region, SDK problems, ...
        log.warning("AI agent not configured: %s", exc)
        app.extensions[_AGENT_KEY] = UnconfiguredAgent(NOT_CONFIGURED)


def get_chat_service() -> ChatService:
    config = current_app.config
    return ChatService(
        db.session,
        current_app.extensions[_AGENT_KEY],
        limits=ChatLimits(
            history_messages=config["AI_HISTORY_MESSAGES"],
            max_messages_per_session=config["AI_MAX_MESSAGES_PER_SESSION"],
            rate_limit_per_minute=config["AI_RATE_LIMIT_PER_MINUTE"],
            daily_message_limit=config["AI_DAILY_MESSAGE_LIMIT"],
        ),
    )
