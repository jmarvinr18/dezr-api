import hmac
import json

from flask import Response, current_app, request, stream_with_context
from flask.views import MethodView
from flask_smorest import Blueprint

from app.extensions.ai import get_chat_service
from app.schema.ai import AiMessageCreateSchema, AiReplySchema
from app.services.ai.agent_client import StreamEvent
from app.services.client import client_ip
from app.services.contact_service import hash_ip
from app.services.errors import ServiceError, Unauthorized

# OpenAPI: "Authorization: Bearer <AI_API_KEY>" when the key is set (see Config.API_SPEC_OPTIONS).
AI_KEY_SECURITY = [{"aiKey": []}]

# These endpoints take a visitor's message and relay it to the agent, which is a separate
# service (dezr-ai: LangGraph on Bedrock AgentCore).
blp = Blueprint(
    "ai_chat",
    __name__,
    url_prefix="/api/v1/ai",
    description="Send a message to the AI assistant (the agent itself, dezr-ai, runs on Bedrock AgentCore)",
)


@blp.before_request
def require_ai_key():
    """With AI_API_KEY set, only callers holding it (dezr-ui's server) may spend model tokens.
    CORS preflights carry no credentials and pass; the request that follows is checked."""
    expected = current_app.config.get("AI_API_KEY")
    if not expected or request.method == "OPTIONS":
        return
    scheme, _, key = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(key.strip().encode(), expected.encode()):
        raise Unauthorized("A valid API key is required for the assistant.")


@blp.after_request
def no_store(response):
    response.headers["Cache-Control"] = "no-store" if response.mimetype != "text/event-stream" else "no-cache"
    return response


def _visitor() -> str | None:
    """Who is asking, for the per-visitor limit: a keyed hash of their IP, never the IP.

    With AI_API_KEY set, the caller has proved it is dezr-ui's server (require_ai_key), so the
    visitor is the address that server saw: the last entry it added to X-Forwarded-For. Nobody
    without the key can pick their own address that way. (Behind TRUSTED_PROXY_COUNT proxies,
    client_ip() already is that address.)
    """
    ip = client_ip()
    if current_app.config.get("AI_API_KEY") and not current_app.config.get("TRUSTED_PROXY_COUNT"):
        forwarded = request.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
        ip = forwarded or ip
    return hash_ip(ip, current_app.config.get("SECRET_KEY") or "")


def _send_args(data: dict) -> dict:
    context = {k: v for k, v in (data["context"] or {}).items() if v} or None
    return {
        "session_id": data["session_id"],
        "agent_id": data["agent_id"],
        "context": context,
        "client_ip_hash": _visitor(),
    }


@blp.route("/messages")
class MessageList(MethodView):
    @blp.doc(security=AI_KEY_SECURITY)
    @blp.arguments(AiMessageCreateSchema)
    @blp.response(201, AiReplySchema)
    @blp.alt_response(401, description="AI_API_KEY is set and the request doesn't carry it")
    @blp.alt_response(404, description="Unknown conversation")
    @blp.alt_response(409, description="The conversation is full; start a new one")
    @blp.alt_response(422, description="Empty or too-long message (max 4000 characters)")
    @blp.alt_response(429, description="Too many messages (per minute, or for the day), or the agent is throttled")
    @blp.alt_response(503, description="The agent is unreachable or not configured")
    def post(self, data):
        """Send a message to the assistant and get its answer

        Omit `sessionId` to start a conversation; the response returns the id to send with the
        next message. The agent searches the firm's documents before answering, so a reply can
        take up to about a minute.
        """
        user_message, assistant_message, session = get_chat_service().send(data["content"], **_send_args(data))
        return {"session_id": session.id, "user_message": user_message, "assistant_message": assistant_message}


def _event(name: str, payload: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, default=str)}\n\n"


def _item_event(item: "str | StreamEvent") -> str:
    """One piece of the agent's stream as an SSE event."""
    if not isinstance(item, StreamEvent):
        return _event("delta", {"text": item})
    if item.kind == "thinking":
        return _event("thinking", {"text": item.data})
    if item.kind == "tool":
        return _event("tool", item.data)
    return _event("todos", {"todos": item.data})


def _done(stored) -> str:
    """The closing event: the stored transcript, exactly as POST /ai/messages returns it."""
    user_message, assistant_message, session = stored
    return _event(
        "done",
        AiReplySchema().dump(
            {"session_id": session.id, "user_message": user_message, "assistant_message": assistant_message}
        ),
    )


def _one_shot(stored) -> list[str]:
    """Both events for an answer that arrived whole, rendered before its rows detach. The text
    is sent as a delta as well, so a client sees the same shape either way."""
    _, assistant_message, _ = stored
    return [_event("delta", {"text": assistant_message.content}), _done(stored)]


@blp.route("/messages/stream")
class MessageStream(MethodView):
    @blp.doc(security=AI_KEY_SECURITY)
    @blp.arguments(AiMessageCreateSchema)
    @blp.response(200, content_type="text/event-stream")
    @blp.alt_response(401, description="AI_API_KEY is set and the request doesn't carry it")
    @blp.alt_response(404, description="Unknown conversation")
    @blp.alt_response(409, description="The conversation is full; start a new one")
    @blp.alt_response(422, description="Empty or too-long message (max 4000 characters)")
    @blp.alt_response(429, description="Too many messages (per minute, or for the day), or the agent is throttled")
    @blp.alt_response(503, description="The agent is unreachable or not configured")
    def post(self, data):
        """Send a message and receive the answer as it is written (server-sent events)

        Same request body as POST /ai/messages. The response is a `text/event-stream`:

            event: delta     data: {"text": "…"}          repeated as the answer arrives
            event: thinking  data: {"text": "…"}          the model's reasoning, as it arrives
            event: tool      data: {id, name, status, input, output}
                                                         a document search; sent again (same
                                                         id) when it completes or fails
            event: todos     data: {"todos": [{id, content, status}]}
            event: done      data: {sessionId, userMessage, assistantMessage}
            event: error     data: {"code": 503, "message": "…"}   if it fails mid-answer

        The conversation is only stored once the answer is complete, so an abandoned stream
        leaves no transcript. Errors raised before the first chunk (unknown conversation, rate
        limit, agent unavailable) are returned as normal HTTP errors.
        """
        stream = get_chat_service().send_stream(data["content"], **_send_args(data))
        # Runs the checks and the agent call, so failures still get a real status code. An agent
        # that streams no deltas at all is already finished here, and its StopIteration has to be
        # caught to keep the return value.
        first = finished = None
        try:
            first = next(stream)
        except StopIteration as stop:
            # The transcript was stored inside this view: render its events now, before the
            # request's database session goes away and leaves the rows unreadable.
            finished = _one_shot(stop.value)

        def events():
            try:
                if finished is not None:
                    yield from finished
                    return
                yield _item_event(first)
                while True:
                    yield _item_event(next(stream))
            except StopIteration as stop:
                yield _done(stop.value)
            except ServiceError as error:
                yield _event("error", {"code": error.status_code, "message": error.message})

        return Response(
            stream_with_context(events()),
            mimetype="text/event-stream",
            # Don't let a proxy buffer the answer into one lump.
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
