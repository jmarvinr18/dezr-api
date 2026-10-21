"""A visitor's side of an AI conversation: store the question, ask the agent, store the answer.

The agent lives in a separate repository (dezr-ai: LangGraph on Bedrock AgentCore); see
app/services/ai/agent_client.py. This service owns the transcript and the limits, nothing
about prompts, models or tools. Ported from vendor-portal-api's chat service; visitors here
are anonymous, so conversations belong to whoever holds their id, and the limits count per
visitor (a keyed hash of their IP) and for everyone together per day.

A question and its answer are stored together only once the answer exists, so a failed agent
call (429/503) leaves nothing half-written and the visitor can retry.
"""

import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.constants import AI_MESSAGE_MAX_LENGTH
from app.model import AiMessage, AiSession
from app.services.ai.agent_client import AgentClient, StreamEvent
from app.services.errors import Conflict, NotFound, TooManyRequests, ValidationFailed

log = logging.getLogger(__name__)

MAX_TITLE_CHARS = 200
DEFAULT_AGENT_ID = "assistant"
NEW_CONVERSATION = "New conversation"


@dataclass
class Turn:
    """One question, checked and ready to send to the agent. A snapshot, so a streamed answer
    doesn't depend on database objects while it arrives."""

    content: str
    session_id: uuid.UUID
    agent_id: str
    context: dict | None
    history: list[dict]
    client_ip_hash: str | None
    is_new: bool = False


@dataclass(frozen=True)
class ChatLimits:
    history_messages: int = 20
    max_messages_per_session: int = 100
    rate_limit_per_minute: int = 10
    daily_message_limit: int = 1000


def _now():
    return datetime.now(timezone.utc)


def _preview(text: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first[:MAX_TITLE_CHARS]


class ChatService:
    def __init__(self, session: Session, agent: AgentClient, *, limits: ChatLimits | None = None):
        self._session = session
        self._agent = agent
        self._limits = limits or ChatLimits()

    def send(
        self,
        content: str,
        *,
        session_id: uuid.UUID | None = None,
        agent_id: str | None = None,
        context: dict | None = None,
        client_ip_hash: str | None = None,
    ) -> tuple[AiMessage, AiMessage, AiSession]:
        """One message and its answer. Without a session_id, a new conversation is started."""
        stream = self.send_stream(
            content, session_id=session_id, agent_id=agent_id, context=context, client_ip_hash=client_ip_hash
        )
        while True:
            try:
                next(stream)
            except StopIteration as stop:
                return stop.value

    def send_stream(
        self,
        content: str,
        *,
        session_id: uuid.UUID | None = None,
        agent_id: str | None = None,
        context: dict | None = None,
        client_ip_hash: str | None = None,
    ) -> Iterator["str | StreamEvent"]:
        """send() as a generator: yields the answer as the agent produces it (text as `str`;
        its thinking, tool calls and to-do list as StreamEvent).

        Used as `user, assistant, session = yield from service.send_stream(...)`. Nothing is
        stored until the answer is complete, so a failed or abandoned stream leaves no
        half-written transcript. The checks and limits run before the first chunk, so a caller
        can still turn them into a normal HTTP error response.
        """
        turn = self._prepare(content, session_id, agent_id, context, client_ip_hash)
        reply = yield from self._agent.stream(str(turn.session_id), self._payload(turn))
        return self._store(turn, reply)

    # ---------- Sessions ----------

    def _prepare(self, content, session_id, agent_id, context, client_ip_hash) -> Turn:
        """Everything the agent call needs, checked before it is made. Writes nothing: a new
        conversation's id is generated here and only stored once the answer exists."""
        content = (content or "").strip()
        if not content:
            raise ValidationFailed("Message cannot be blank.", errors={"content": "Cannot be blank."})
        if len(content) > AI_MESSAGE_MAX_LENGTH:
            raise ValidationFailed(f"Messages are limited to {AI_MESSAGE_MAX_LENGTH} characters.")
        self._check_limits(client_ip_hash)

        if session_id:
            ai_session = self._session.get(AiSession, session_id)
            if ai_session is None:
                raise NotFound("Conversation not found.")
            if ai_session.message_count + 2 > self._limits.max_messages_per_session:
                raise Conflict("This conversation is full. Please start a new one.")
            return Turn(
                content=content,
                session_id=ai_session.id,
                agent_id=ai_session.agent_id,
                context=ai_session.context,
                history=self._history(ai_session),
                client_ip_hash=client_ip_hash,
            )

        return Turn(
            content=content,
            session_id=uuid.uuid4(),
            # Which agent the message is for. The catalogue lives in dezr-ai, so this is stored
            # and forwarded as-is.
            agent_id=(agent_id or "").strip()[:50] or DEFAULT_AGENT_ID,
            context=context or None,
            history=[],
            client_ip_hash=client_ip_hash,
            is_new=True,
        )

    def _store(self, turn: Turn, reply) -> tuple[AiMessage, AiMessage, AiSession]:
        """Writes the question, the answer and the conversation in one transaction.

        Called only once the answer is complete, so nothing is written for a failed or
        abandoned turn. The conversation is re-read here because a streamed answer arrives
        long after _prepare()."""
        now = _now()
        ai_session = None if turn.is_new else self._session.get(AiSession, turn.session_id)
        if ai_session is None:
            ai_session = AiSession(
                id=turn.session_id,
                agent_id=turn.agent_id,
                title=_preview(turn.content) or NEW_CONVERSATION,
                context=turn.context,
                message_count=0,
                created_at=now,
            )
            self._session.add(ai_session)

        user_message = AiMessage(
            session=ai_session,
            sequence=ai_session.message_count + 1,
            role="user",
            content=turn.content,
            client_ip_hash=turn.client_ip_hash,
            created_at=now,
        )
        assistant_message = AiMessage(
            session=ai_session,
            sequence=ai_session.message_count + 2,
            role="assistant",
            content=reply.text,
            citations=reply.citations or None,
            model=reply.model or self._agent.name,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            tool_calls=reply.tool_calls,
            reasoning=reply.reasoning,
            steps=reply.steps or None,
            todos=reply.todos or None,
            created_at=now,
        )
        self._session.add_all([user_message, assistant_message])
        ai_session.message_count += 2
        ai_session.preview = _preview(reply.text)
        ai_session.updated_at = now
        self._session.commit()
        log.info(
            "AI reply session=%s agent=%s in=%s out=%s tools=%s",
            ai_session.id, ai_session.agent_id, reply.input_tokens, reply.output_tokens, reply.tool_calls,
        )
        return user_message, assistant_message, ai_session

    # ---------- Limits and the agent payload ----------

    def _check_limits(self, client_ip_hash: str | None) -> None:
        questions = select(func.count(AiMessage.id)).where(AiMessage.role == "user")
        if client_ip_hash and self._limits.rate_limit_per_minute > 0:
            recent = self._session.scalar(
                questions.where(
                    AiMessage.client_ip_hash == client_ip_hash,
                    AiMessage.created_at >= _now() - timedelta(minutes=1),
                )
            )
            if recent >= self._limits.rate_limit_per_minute:
                raise TooManyRequests("You're sending messages too quickly. Please wait a minute and try again.")
        if self._limits.daily_message_limit > 0:
            today = self._session.scalar(questions.where(AiMessage.created_at >= _now() - timedelta(days=1)))
            if today >= self._limits.daily_message_limit:
                log.warning("AI daily message limit (%s) reached", self._limits.daily_message_limit)
                raise TooManyRequests("The assistant has answered all it can for today. Please try again tomorrow.")

    def _payload(self, turn: Turn) -> dict:
        """What the agent receives.

        `history` is the conversation before this message (the new one is `message`), sent so a
        stateless agent still has the context. dezr-ai keeps its own memory per session id (its
        LangGraph checkpointer) and reads only `message` and `sessionId`."""
        return {
            "message": turn.content,
            "sessionId": str(turn.session_id),
            "agentId": turn.agent_id,
            "context": turn.context or {},
            "history": turn.history,
        }

    def _history(self, ai_session: AiSession) -> list[dict]:
        messages = ai_session.messages[-self._limits.history_messages:]
        # The transcript must start with a question.
        while messages and messages[0].role != "user":
            messages = messages[1:]
        return [{"role": m.role, "content": m.content} for m in messages]
