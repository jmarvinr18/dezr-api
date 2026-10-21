"""Test doubles for the agent service: no AWS or HTTP calls, deterministic replies."""

import io
import json
from dataclasses import replace

from app.services.ai.agent_client import AgentClient, AgentReply, StreamEvent


def reply(text: str, **fields) -> AgentReply:
    return AgentReply(text=text, **fields)


class ScriptedAgent(AgentClient):
    """Returns the scripted replies in order and records every payload it was sent."""

    name = "fake-agent"

    def __init__(self, *replies: AgentReply, error: Exception | None = None):
        self.replies = list(replies)
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def invoke(self, session_id: str, payload: dict) -> AgentReply:
        self.calls.append((session_id, payload))
        if self.error:
            raise self.error
        if not self.replies:
            raise AssertionError("Unexpected extra agent call")
        return self.replies.pop(0)

    @property
    def payload(self) -> dict:
        """The last payload sent to the agent."""
        return self.calls[-1][1]


class StreamingAgent(AgentClient):
    """Streams one scripted answer as chunks, like an agent that sends `text/event-stream`."""

    name = "fake-streaming-agent"

    def __init__(self, *chunks, final: AgentReply | None = None, error: Exception | None = None):
        self.chunks = list(chunks)
        self.final = final
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def invoke(self, session_id: str, payload: dict) -> AgentReply:
        raise AssertionError("The streaming fake should be used through stream()")

    def stream(self, session_id: str, payload: dict):
        """Chunks are answer text (str) or StreamEvent."""
        self.calls.append((session_id, payload))
        if self.error:
            raise self.error
        thinking, steps, todos = [], {}, []
        for chunk in self.chunks:
            if isinstance(chunk, StreamEvent):
                if chunk.kind == "tool":
                    steps[chunk.data["id"]] = {**steps.get(chunk.data["id"], {}), **chunk.data}
                elif chunk.kind == "todos":
                    todos = chunk.data
                else:
                    thinking.append(chunk.data)
            yield chunk
        text = "".join(c for c in self.chunks if isinstance(c, str))
        streamed = dict(reasoning="".join(thinking) or None, steps=list(steps.values()), todos=todos)
        if self.final is None:
            return AgentReply(text=text, model=self.name, **streamed)
        # Like a real stream: the final object adds citations and usage to the chunks.
        return replace(self.final, text=self.final.text or text, **streamed)

    @property
    def payload(self) -> dict:
        return self.calls[-1][1]


# ---------- A stand-in for boto3's bedrock-agentcore client ----------


def sse(*events: dict) -> bytes:
    """A text/event-stream body as BedrockAgentCoreApp frames it: one `data:` line per event."""
    return b"".join(f"data: {json.dumps(e)}\n\n".encode() for e in events)


class FakeBody:
    """botocore's StreamingBody: read() and iter_lines()."""

    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def iter_lines(self):
        yield from io.BytesIO(self._data).read().splitlines()


class FakeAgentCore:
    """Records invoke_agent_runtime calls and answers with a canned body."""

    def __init__(self, body: bytes, content_type: str = "text/event-stream", error: Exception | None = None):
        self.body = body
        self.content_type = content_type
        self.error = error
        self.calls: list[dict] = []

    def invoke_agent_runtime(self, **kwargs):
        self.calls.append({**kwargs, "payload": json.loads(kwargs["payload"])})
        if self.error:
            raise self.error
        return {"response": FakeBody(self.body), "contentType": self.content_type}
