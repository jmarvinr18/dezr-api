"""The port to the AI agent, and its adapters.

The agent lives in a separate repository (dezr-ai: LangGraph, hosted on Amazon Bedrock
AgentCore Runtime). This API never talks to a model: it hands the visitor's message to the
agent and stores what comes back. Same contract as vendor-portal-api's agent client.

What the agent receives (dezr-ai's main.py reads `message`, `sessionId` and `stream`):

    {"message": "…", "sessionId": "…", "stream": true, "agentId": "…", "context": {…},
     "history": [{"role": "user" | "assistant", "content": "…"}]}

While streaming (`text/event-stream`), each `data:` line is one of:

    {"delta": "…"}                                answer text
    {"thinking": "…"}                             the model's reasoning, shown collapsed
    {"tool": {"id", "name", "status", "input", "output"}}
                                                  a tool call (dezr-ai: search_documents);
                                                  status is started, then completed or
                                                  failed (same id)
    {"todos": [{"id", "content", "status"}]}      the agent's to-do list, whole, each time
    {"reply": "…", "citations": [{"type": "document", "id": "…", "label": "…"}],
     "model": "…", "usage": {"inputTokens": 0, "outputTokens": 0}, "toolCalls": 0}
                                                  once at the end

Without streaming the agent answers with that final object as JSON. A LangGraph agent that
returns its raw state instead ({"result": "…", "messages": [...]}, dezr-ai with
"stream": false) is also read: the reply comes from `result`, and the model and token usage
from the messages after the last human one.

Adapters map transport failures to AgentUnavailable / TooManyRequests, so callers never
handle boto3 or urllib exception types.
"""

import json
import logging
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from app.constants import AI_CITATION_TYPES
from app.services.errors import AgentUnavailable, TooManyRequests

log = logging.getLogger(__name__)

MAX_REPLY_CHARS = 20_000
MAX_CITATIONS = 10
MAX_REASONING_CHARS = 20_000
MAX_STEPS = 25
MAX_STEP_TEXT = 500
TOOL_STATUSES = {"started", "completed", "failed"}
MAX_TODOS = 20
MAX_TODO_CHARS = 200
TODO_STATUSES = {"pending", "in_progress", "completed"}

UNAVAILABLE = "The AI assistant is unavailable right now. Please try again."
BUSY = "The AI assistant is busy. Please try again in a minute."


@dataclass
class AgentReply:
    text: str
    citations: list[dict] = field(default_factory=list)
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    tool_calls: int | None = None
    reasoning: str | None = None
    # [{"id", "name", "status", "input", "output"}]
    steps: list[dict] = field(default_factory=list)
    # [{"id", "content", "status"}]: the agent's plan for the answer, as it last stood
    todos: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class StreamEvent:
    """Something other than answer text in the agent's stream (answer text is a plain str)."""

    kind: str  # "thinking" | "tool" | "todos"
    data: object


class AgentClient(ABC):
    """What the chat service depends on. `name` only labels stored messages."""

    name: str

    @abstractmethod
    def invoke(self, session_id: str, payload: dict) -> AgentReply:
        """Send one message to the agent. Raises AgentUnavailable or TooManyRequests."""

    def stream(self, session_id: str, payload: dict) -> Iterator["str | StreamEvent"]:
        """Yield the answer as it arrives; return the finished AgentReply.

        Answer text is yielded as `str`; thinking, tool calls and the to-do list as
        StreamEvent. Used as `reply = yield from client.stream(...)`. The default is for agents
        that don't stream: one chunk with the whole answer, so callers need no special case.
        """
        reply = self.invoke(session_id, payload)
        yield reply.text
        return reply


def parse_reply(body: bytes | str, fallback_model: str | None = None) -> AgentReply:
    """Reads the agent's JSON response. A plain (non-JSON) body is taken as the reply text."""
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        # A bare string body (or a JSON string) is still a usable answer.
        reply = data if isinstance(data, str) else text
        return AgentReply(text=_clean_text(reply), model=fallback_model)
    _raise_if_error(data)
    parsed = _reply_from(data, fallback_model)
    # A complete response must carry an answer; a streamed final object need not (stream_sse).
    parsed.text = _clean_text(parsed.text)
    return parsed


def _raise_if_error(data: dict) -> None:
    """The agent failed: the AgentCore SDK (BedrockAgentCoreApp) answers an exception in the
    agent with {"error": "...", "error_type": "...", "message": "An error occurred during
    streaming"}. That `message` is not an answer, so it must never be stored as one. The detail
    is logged here; the visitor only learns that the assistant couldn't answer."""
    if data.get("error"):
        log.error(
            "The agent reported an error: %s: %s",
            data.get("error_type") or "error", str(data.get("error"))[:1000],
        )
        raise AgentUnavailable(UNAVAILABLE)


def _reply_from(data: dict, fallback_model: str | None) -> AgentReply:
    """An AgentReply from the agent's JSON object, without requiring the text to be there."""
    reply = data.get("reply") or data.get("message") or data.get("output") or data.get("result") or ""
    if not isinstance(reply, str):
        reply = json.dumps(reply, ensure_ascii=False)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    # A LangGraph state dump carries no `usage`/`model`; read them off its messages instead.
    derived = _langgraph_metadata(data)
    return AgentReply(
        text=reply.strip()[:MAX_REPLY_CHARS],
        citations=_clean_citations(data.get("citations")),
        model=data.get("model") or derived.get("model") or fallback_model,
        input_tokens=_first(
            _as_int(usage.get("inputTokens")), _as_int(usage.get("input_tokens")), derived.get("input_tokens")
        ),
        output_tokens=_first(
            _as_int(usage.get("outputTokens")), _as_int(usage.get("output_tokens")), derived.get("output_tokens")
        ),
        tool_calls=_first(
            _as_int(data.get("toolCalls")), _as_int(data.get("tool_calls")), derived.get("tool_calls")
        ),
        reasoning=_clean_reasoning(data.get("thinking") or data.get("reasoning")),
        steps=[s for s in map(clean_step, _as_list(data.get("steps"))) if s][:MAX_STEPS],
        todos=clean_todos(data.get("todos")),
    )


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _clip(value, limit: int = MAX_STEP_TEXT) -> str | None:
    """A short, display-only summary of a tool's input or output."""
    if value is None or value == "":
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _clean_reasoning(value) -> str | None:
    return value.strip()[:MAX_REASONING_CHARS] or None if isinstance(value, str) else None


def clean_step(step) -> dict | None:
    """A tool call as the UI shows it. Raw tool output can be long or contain anything the
    tool read, so only a clipped text summary is kept."""
    if not isinstance(step, dict):
        return None
    step_id, name = step.get("id"), step.get("name")
    if not step_id or not isinstance(name, str) or not name.strip():
        return None
    status = step.get("status") if step.get("status") in TOOL_STATUSES else "started"
    return {
        "id": str(step_id)[:100],
        "name": name.strip()[:100],
        "status": status,
        "input": _clip(step.get("input")),
        "output": _clip(step.get("output")),
    }


def clean_todos(todos) -> list[dict]:
    """The agent's to-do list as the UI shows it. Items without text are dropped, an unknown
    status counts as pending, and an item without an id is numbered by its position."""
    cleaned = []
    for position, todo in enumerate(_as_list(todos)[:MAX_TODOS], 1):
        if not isinstance(todo, dict):
            continue
        content = todo.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        cleaned.append({
            "id": str(todo.get("id") or position)[:100],
            "content": content.strip()[:MAX_TODO_CHARS],
            "status": todo.get("status") if todo.get("status") in TODO_STATUSES else "pending",
        })
    return cleaned


def _langgraph_metadata(data: dict) -> dict:
    """Model and token usage read off a LangGraph state dump's `messages`.

    Only the messages after the last human one are counted. `messages` replays the agent's
    whole thread, so summing the list would charge earlier turns to this answer.
    """
    messages = data.get("messages")
    if not isinstance(messages, list):
        return {}

    turn: list[dict] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("type") == "human":
            turn = []
        else:
            turn.append(message)

    model, tool_calls = None, 0
    input_tokens, output_tokens, counted = 0, 0, False
    for message in turn:
        if message.get("type") == "tool":
            tool_calls += 1
            continue
        if message.get("type") != "ai":
            continue
        metadata = message.get("response_metadata")
        if isinstance(metadata, dict) and metadata.get("model_name"):
            model = metadata["model_name"]
        usage = message.get("usage_metadata")
        if isinstance(usage, dict):
            given = _as_int(usage.get("input_tokens")), _as_int(usage.get("output_tokens"))
            if any(value is not None for value in given):
                counted = True
                input_tokens += given[0] or 0
                output_tokens += given[1] or 0

    return {
        "model": model,
        "input_tokens": input_tokens if counted else None,
        "output_tokens": output_tokens if counted else None,
        "tool_calls": tool_calls if turn else None,
    }


DELTA_KEYS = ("delta", "chunk", "token", "text", "content")
FINAL_KEYS = ("reply", "message", "output", "result", "citations", "usage", "model", "toolCalls")


def stream_sse(
    lines: Iterable[bytes | str], fallback_model: str | None = None
) -> Iterator["str | StreamEvent"]:
    """Yield an agent's `text/event-stream` as it arrives; return the finished AgentReply.

    Each `data:` line is a piece of the answer (a bare string, or an object with `delta` /
    `chunk` / `token` / `text` / `content`), yielded as `str`; reasoning, a tool call or the
    to-do list (`thinking` / `tool` / `todos`), yielded as StreamEvent; or the final object
    described at the top of this module, which carries the citations and usage. Comments,
    `event:` lines, heartbeats, `[DONE]` and kinds of event this API doesn't know are ignored.
    """
    pieces: list[str] = []
    thinking: list[str] = []
    steps: dict[str, dict] = {}  # by tool call id, in first-seen order
    todos: list[dict] = []  # each event is the whole list, so only the latest is kept
    final: AgentReply | None = None

    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        # Only the line ending is stripped: spaces inside a chunk are part of the answer.
        line = line.rstrip("\r\n")
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line[len("data:"):]
        # One optional space after the colon belongs to the protocol, not the data.
        data = data[1:] if data.startswith(" ") else data
        if not data.strip() or data.strip() == "[DONE]":
            continue

        try:
            parsed = json.loads(data)
        except ValueError:
            pieces.append(data)
            yield data
            continue

        if isinstance(parsed, str):
            pieces.append(parsed)
            yield parsed
        elif isinstance(parsed, dict):
            # Before anything else: an error event also carries `message`, a final-object key.
            _raise_if_error(parsed)
            if any(key in parsed for key in FINAL_KEYS):
                final = _reply_from(parsed, fallback_model)
                continue
            if isinstance(parsed.get("thinking"), str):
                if parsed["thinking"] and sum(map(len, thinking)) < MAX_REASONING_CHARS:
                    thinking.append(parsed["thinking"])
                    yield StreamEvent("thinking", parsed["thinking"])
                continue
            if "tool" in parsed:
                step = clean_step(parsed["tool"])
                if step and (step["id"] in steps or len(steps) < MAX_STEPS):
                    # A later event for the same call (completed / failed) updates it in place.
                    merged = {**steps.get(step["id"], {}), **{k: v for k, v in step.items() if v}}
                    steps[step["id"]] = merged
                    yield StreamEvent("tool", merged)
                continue
            if "todos" in parsed:
                if isinstance(parsed["todos"], list):
                    todos = clean_todos(parsed["todos"])
                    yield StreamEvent("todos", todos)
                continue
            piece = next(
                (parsed[key] for key in DELTA_KEYS if isinstance(parsed.get(key), str) and parsed[key]),
                "",
            )
            if piece:
                pieces.append(piece)
                yield piece

    # The deltas are the answer; the final object only adds citations and usage. An agent that
    # sends no deltas at all (just the final object) still gives us its text.
    text = "".join(pieces) or (final.text if final else "")
    return AgentReply(
        text=_clean_text(text),
        citations=final.citations if final else [],
        model=(final.model if final else None) or fallback_model,
        input_tokens=final.input_tokens if final else None,
        output_tokens=final.output_tokens if final else None,
        tool_calls=_first(final.tool_calls if final else None, len(steps) or None),
        reasoning=_clean_reasoning("".join(thinking)) or (final.reasoning if final else None),
        steps=list(steps.values()) or (final.steps if final else []),
        # The final object's list is how the plan ended; the last streamed one otherwise.
        todos=(final.todos if final else []) or todos,
    )


def _first(*values):
    """The first value that was actually given; 0 is a value, None is not."""
    return next((value for value in values if value is not None), None)


# Model reasoning markup (Nova's <thinking>) that leaked into the answer: a whole block, or a
# stray tag left on its own. The reasoning itself arrives separately as `thinking`.
THINKING_MARKUP = re.compile(r"<thinking>.*?</thinking>|</?thinking>", re.DOTALL | re.IGNORECASE)


def _clean_text(reply: str) -> str:
    reply = THINKING_MARKUP.sub("", reply or "").strip()
    if not reply:
        raise AgentUnavailable("The AI assistant returned an empty answer. Please try again.")
    return reply[:MAX_REPLY_CHARS]


def _clean_citations(citations) -> list[dict]:
    if not isinstance(citations, list):
        return []
    cleaned = []
    for citation in citations[:MAX_CITATIONS]:
        if not isinstance(citation, dict):
            continue
        kind, id_, label = citation.get("type"), citation.get("id"), citation.get("label")
        if kind in AI_CITATION_TYPES and id_:
            cleaned.append({"type": kind, "id": str(id_)[:100], "label": str(label or id_)[:200]})
    return cleaned


def _as_int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# ---------- Amazon Bedrock AgentCore Runtime ----------


class AgentCoreClient(AgentClient):
    """Invokes the agent deployed to Bedrock AgentCore Runtime (dezr-ai).

    The conversation id is passed as `runtimeSessionId`, so AgentCore routes every message in
    one conversation to the same runtime session, and dezr-ai's LangGraph checkpointer keys on
    it (it also reads `sessionId` from the payload).
    """

    def __init__(self, client, runtime_arn: str, *, qualifier: str = "DEFAULT"):
        self._client = client
        self._arn = runtime_arn
        self._qualifier = qualifier
        self.name = runtime_arn.rsplit("/", 1)[-1]

    def _call(self, session_id: str, payload: dict, accept: str):
        return self._client.invoke_agent_runtime(
            agentRuntimeArn=self._arn,
            qualifier=self._qualifier,
            # AgentCore requires at least 33 characters; a UUID string is 36.
            runtimeSessionId=session_id,
            contentType="application/json",
            accept=accept,
            payload=json.dumps(payload, default=str).encode(),
        )

    def invoke(self, session_id: str, payload: dict) -> AgentReply:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            # dezr-ai streams unless told not to; its JSON answer is then the LangGraph state.
            response = self._call(session_id, {**payload, "stream": False}, "application/json")
            body = response["response"]
            if "text/event-stream" in (response.get("contentType") or ""):
                return _drain(stream_sse(body.iter_lines(), fallback_model=self.name))
            return parse_reply(body.read(), fallback_model=self.name)
        except ClientError as exc:
            raise self._transport_error(exc) from exc
        except BotoCoreError as exc:
            log.warning("AgentCore invoke failed: %s", exc)
            raise AgentUnavailable(UNAVAILABLE) from exc

    def stream(self, session_id: str, payload: dict) -> Iterator["str | StreamEvent"]:
        """Asks AgentCore for `text/event-stream`. An agent that answers in one piece still
        works: the body is then read as a normal response."""
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            response = self._call(session_id, {**payload, "stream": True}, "text/event-stream")
            body = response["response"]
            if "text/event-stream" not in (response.get("contentType") or ""):
                reply = parse_reply(body.read(), fallback_model=self.name)
                yield reply.text
                return reply
            return (yield from stream_sse(body.iter_lines(), fallback_model=self.name))
        except ClientError as exc:
            raise self._transport_error(exc) from exc
        except BotoCoreError as exc:
            log.warning("AgentCore stream failed: %s", exc)
            raise AgentUnavailable(UNAVAILABLE) from exc

    @staticmethod
    def _transport_error(exc) -> Exception:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("ThrottlingException", "TooManyRequestsException"):
            return TooManyRequests(BUSY)
        log.error("AgentCore invoke failed (%s): %s", code, exc)
        return AgentUnavailable(UNAVAILABLE)


def _drain(stream: Iterator) -> AgentReply:
    while True:
        try:
            next(stream)
        except StopIteration as stop:
            return stop.value


# ---------- Plain HTTP ----------


class HttpAgentClient(AgentClient):
    """POSTs to an HTTP endpoint: dezr-ai running locally (`python main.py`, port 8080
    /invocations), or behind a load balancer."""

    def __init__(self, url: str, *, timeout: float = 120, token: str | None = None):
        self._url = url
        self._timeout = timeout
        self._token = token
        self.name = "http-agent"

    def _request(self, session_id: str, payload: dict, accept: str) -> urllib.request.Request:
        headers = {"Content-Type": "application/json", "Accept": accept, "X-Session-Id": session_id}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return urllib.request.Request(
            self._url, data=json.dumps(payload, default=str).encode(), headers=headers, method="POST"
        )

    def invoke(self, session_id: str, payload: dict) -> AgentReply:
        request = self._request(session_id, {**payload, "stream": False}, "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                if response.headers.get_content_type() == "text/event-stream":
                    return _drain(stream_sse(response, fallback_model=self.name))
                return parse_reply(response.read(), fallback_model=self.name)
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            log.warning("Agent request failed: %s", exc)
            raise AgentUnavailable(UNAVAILABLE) from exc

    def stream(self, session_id: str, payload: dict) -> Iterator["str | StreamEvent"]:
        """Asks the endpoint for `text/event-stream`; falls back to reading the whole body
        when it answers with anything else."""
        request = self._request(session_id, {**payload, "stream": True}, "text/event-stream")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                if response.headers.get_content_type() != "text/event-stream":
                    reply = parse_reply(response.read(), fallback_model=self.name)
                    yield reply.text
                    return reply
                return (yield from stream_sse(response, fallback_model=self.name))
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            log.warning("Agent stream failed: %s", exc)
            raise AgentUnavailable(UNAVAILABLE) from exc

    @staticmethod
    def _http_error(exc: urllib.error.HTTPError) -> Exception:
        if exc.code == 429:
            return TooManyRequests(BUSY)
        log.error("Agent returned %s: %s", exc.code, exc.read()[:500])
        return AgentUnavailable(UNAVAILABLE)


class UnconfiguredAgent(AgentClient):
    """Stand-in when no agent is configured: every message fails with a clear 503."""

    name = "unconfigured"

    def __init__(self, reason: str):
        self._reason = reason

    def invoke(self, session_id: str, payload: dict) -> AgentReply:
        raise AgentUnavailable(self._reason)
