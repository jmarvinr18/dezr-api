"""POST /api/v1/ai/messages[/stream]: the chat dezr-ui shows, relayed to the dezr-ai agent."""

import json
import uuid

import pytest
from botocore.exceptions import ClientError

from app.database import db
from app.model import AiMessage, AiSession
from app.services.ai.agent_client import AgentCoreClient, StreamEvent, stream_sse
from app.services.errors import AgentUnavailable, TooManyRequests
from tests.ai_fakes import FakeAgentCore, ScriptedAgent, StreamingAgent, reply, sse

API = "/api/v1/ai/messages"
STREAM = "/api/v1/ai/messages/stream"
RUNTIME_ARN = "arn:aws:bedrock-agentcore:ap-southeast-1:123456789012:runtime/dezr_ai_agent-oWJlg6Fpai"

# What dezr-ai's streaming.py sends for one RAG answer.
DEZR_AI_STREAM = sse(
    {"thinking": "The user asks about incorporation; search the documents."},
    {"tool": {"id": "run-1", "name": "search_documents", "status": "started", "input": "{\"query\": \"incorporation\"}"}},
    {"tool": {"id": "run-1", "name": "search_documents", "status": "completed", "output": "Doing Business Guide > Corporate ..."}},
    {"delta": "Foreign companies "},
    {"delta": "can register a branch or a subsidiary."},
    {
        "reply": "Foreign companies can register a branch or a subsidiary.",
        "citations": [
            {"type": "document", "id": "6f1d1c2e-8b7d-4e21-9a0f-5c4b3a2d1e0f", "label": "Doing Business Guide"},
            {"type": "invoice", "id": "x", "label": "not a kind this site cites"},
        ],
        "model": "apac.amazon.nova-lite-v1:0",
        "usage": {"inputTokens": 1200, "outputTokens": 60},
        "toolCalls": 1,
    },
)


@pytest.fixture
def use_agent(app):
    def install(agent):
        app.extensions["ai_agent_client"] = agent
        return agent

    return install


def events(response):
    """The stream parsed into (event name, data) pairs."""
    parsed = []
    for block in response.get_data(as_text=True).split("\n\n"):
        lines = [line for line in block.splitlines() if line]
        if not lines:
            continue
        name = next((line[len("event: "):] for line in lines if line.startswith("event: ")), "message")
        data = next((line[len("data: "):] for line in lines if line.startswith("data: ")), "")
        parsed.append((name, json.loads(data) if data else None))
    return parsed


def done_of(response):
    return next(data for name, data in events(response) if name == "done")


# ---------- POST /ai/messages ----------


def test_message_and_answer_are_stored(app, client, use_agent):
    agent = use_agent(ScriptedAgent(reply("We advise on incorporation.", citations=[], model="nova")))

    response = client.post(API, json={"content": "  What do you do?  "})

    assert response.status_code == 201
    body = response.get_json()
    assert body["userMessage"]["content"] == "What do you do?"
    assert body["assistantMessage"]["content"] == "We advise on incorporation."
    assert body["assistantMessage"]["steps"] == [] and body["assistantMessage"]["citations"] == []
    # The agent gets the visitor's message and the conversation id (AgentCore's session).
    session_id, payload = agent.calls[0]
    assert payload["message"] == "What do you do?" and payload["sessionId"] == body["sessionId"] == session_id
    assert payload["history"] == [] and payload["agentId"] == "assistant"
    with app.app_context():
        stored = db.session.get(AiSession, uuid.UUID(body["sessionId"]))
        assert (stored.message_count, stored.title, stored.preview) == (2, "What do you do?", "We advise on incorporation.")


def test_follow_up_sends_the_history(client, use_agent):
    agent = use_agent(ScriptedAgent(reply("First."), reply("Second.")))
    first = client.post(API, json={"content": "Hello"}).get_json()

    second = client.post(API, json={"content": "And then?", "sessionId": first["sessionId"]}).get_json()

    assert second["sessionId"] == first["sessionId"]
    assert agent.payload["history"] == [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "First."}]


def test_validation_and_unknown_conversations(client, use_agent):
    use_agent(ScriptedAgent())
    assert client.post(API, json={"content": "   "}).status_code == 422
    assert client.post(API, json={"content": "x" * 4001}).status_code == 422
    assert client.post(API, json={"content": "Hi", "invoiceId": "x"}).status_code == 422  # unknown field
    unknown = client.post(API, json={"content": "Hi", "sessionId": str(uuid.uuid4())})
    assert unknown.status_code == 404


def test_agent_failures_store_nothing(app, client, use_agent):
    use_agent(ScriptedAgent(error=AgentUnavailable("The AI assistant is unavailable right now. Please try again.")))
    response = client.post(API, json={"content": "Hello"})
    assert response.status_code == 503
    use_agent(ScriptedAgent(error=TooManyRequests("busy")))
    assert client.post(API, json={"content": "Hello"}).status_code == 429
    with app.app_context():
        assert db.session.query(AiMessage).count() == 0


def test_unconfigured_agent_is_503(client):
    response = client.post(API, json={"content": "Hello"})
    assert response.status_code == 503
    assert "isn't configured" in response.get_json()["message"]


def test_per_visitor_rate_limit(app, client, use_agent):
    app.config["AI_RATE_LIMIT_PER_MINUTE"] = 2
    use_agent(ScriptedAgent(reply("a"), reply("b"), reply("c")))
    assert client.post(API, json={"content": "1"}).status_code == 201
    assert client.post(API, json={"content": "2"}).status_code == 201
    limited = client.post(API, json={"content": "3"})
    assert limited.status_code == 429 and "too quickly" in limited.get_json()["message"]


def test_daily_limit_for_everyone(app, client, use_agent):
    app.config.update(AI_RATE_LIMIT_PER_MINUTE=0, AI_DAILY_MESSAGE_LIMIT=1)
    use_agent(ScriptedAgent(reply("a"), reply("b")))
    assert client.post(API, json={"content": "1"}).status_code == 201
    capped = client.post(API, json={"content": "2"}, environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert capped.status_code == 429 and "for today" in capped.get_json()["message"]


def test_full_conversation_is_409(app, client, use_agent):
    app.config["AI_MAX_MESSAGES_PER_SESSION"] = 2
    use_agent(ScriptedAgent(reply("a"), reply("b")))
    first = client.post(API, json={"content": "1"}).get_json()
    assert client.post(API, json={"content": "2", "sessionId": first["sessionId"]}).status_code == 409


def test_api_key_guards_the_endpoints_when_set(app, client, use_agent):
    app.config["AI_API_KEY"] = "k" * 40
    use_agent(ScriptedAgent(reply("ok")))
    assert client.post(API, json={"content": "Hi"}).status_code == 401
    assert client.post(API, json={"content": "Hi"}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post(STREAM, json={"content": "Hi"}).status_code == 401
    ok = client.post(API, json={"content": "Hi"}, headers={"Authorization": f"Bearer {'k' * 40}"})
    assert ok.status_code == 201


# ---------- POST /ai/messages/stream ----------


def test_stream_relays_deltas_steps_and_thinking_then_done(app, client, use_agent):
    use_agent(
        StreamingAgent(
            StreamEvent("thinking", "Searching."),
            StreamEvent("tool", {"id": "t1", "name": "search_documents", "status": "started", "input": "fees"}),
            StreamEvent("tool", {"id": "t1", "name": "search_documents", "status": "completed", "output": "3 passages"}),
            "Fees are ",
            "agreed in writing.",
            final=reply("", citations=[{"type": "document", "id": "d1", "label": "Engagement Guide"}], model="nova"),
        )
    )

    response = client.post(STREAM, json={"content": "How are fees set?"})

    assert response.status_code == 200 and response.mimetype == "text/event-stream"
    assert response.headers["X-Accel-Buffering"] == "no"
    parsed = events(response)
    assert [name for name, _ in parsed] == ["thinking", "tool", "tool", "delta", "delta", "done"]
    done = parsed[-1][1]
    answer = done["assistantMessage"]
    assert answer["content"] == "Fees are agreed in writing."
    assert answer["thinking"] == "Searching."
    assert answer["steps"] == [
        {"id": "t1", "name": "search_documents", "status": "completed", "input": "fees", "output": "3 passages"}
    ]
    assert answer["citations"] == [{"type": "document", "id": "d1", "label": "Engagement Guide"}]


def test_stream_errors_before_the_first_chunk_are_http_errors(client, use_agent):
    use_agent(StreamingAgent(error=AgentUnavailable("down")))
    assert client.post(STREAM, json={"content": "Hi"}).status_code == 503


def test_stream_error_mid_answer_is_an_error_event_and_stores_nothing(app, client, use_agent):
    class Failing(StreamingAgent):
        def stream(self, session_id, payload):
            yield "Partial "
            raise AgentUnavailable("The AI assistant is unavailable right now. Please try again.")

    use_agent(Failing())
    parsed = events(client.post(STREAM, json={"content": "Hi"}))
    assert parsed[-1] == ("error", {"code": 503, "message": "The AI assistant is unavailable right now. Please try again."})
    with app.app_context():
        assert db.session.query(AiMessage).count() == 0


def test_an_agent_that_streams_only_a_final_object_still_completes(client, use_agent):
    use_agent(StreamingAgent(final=reply("The whole answer at once.")))
    parsed = events(client.post(STREAM, json={"content": "Hi"}))
    assert [d["text"] for n, d in parsed if n == "delta"] == ["The whole answer at once."]
    assert parsed[-1][1]["assistantMessage"]["content"] == "The whole answer at once."


# ---------- The AgentCore adapter, against dezr-ai's real event format ----------


def test_agentcore_stream_reads_dezr_ai_events(app, client, use_agent):
    agentcore = FakeAgentCore(DEZR_AI_STREAM)
    use_agent(AgentCoreClient(agentcore, RUNTIME_ARN))

    done = done_of(client.post(STREAM, json={"content": "How do I incorporate?"}))

    [call] = agentcore.calls
    assert call["agentRuntimeArn"] == RUNTIME_ARN and call["qualifier"] == "DEFAULT"
    assert call["accept"] == "text/event-stream"
    # The conversation id is AgentCore's session (33+ characters) and dezr-ai's thread id.
    assert call["runtimeSessionId"] == done["sessionId"] and len(call["runtimeSessionId"]) >= 33
    assert call["payload"]["message"] == "How do I incorporate?"
    assert call["payload"]["sessionId"] == done["sessionId"] and call["payload"]["stream"] is True

    answer = done["assistantMessage"]
    assert answer["content"] == "Foreign companies can register a branch or a subsidiary."
    assert answer["citations"] == [
        {"type": "document", "id": "6f1d1c2e-8b7d-4e21-9a0f-5c4b3a2d1e0f", "label": "Doing Business Guide"}
    ]
    assert [s["status"] for s in answer["steps"]] == ["completed"] and answer["thinking"].startswith("The user asks")
    with app.app_context():
        stored = db.session.query(AiMessage).filter_by(role="assistant").one()
        assert (stored.model, stored.input_tokens, stored.output_tokens, stored.tool_calls) == (
            "apac.amazon.nova-lite-v1:0", 1200, 60, 1
        )


def test_agentcore_invoke_asks_dezr_ai_not_to_stream(client, use_agent):
    # dezr-ai with "stream": false answers with its LangGraph state.
    state = {
        "result": "We are in Makati City.",
        "messages": [
            {"type": "human", "content": "Where are you?"},
            {"type": "ai", "content": "We are in Makati City.", "response_metadata": {"model_name": "nova-lite"},
             "usage_metadata": {"input_tokens": 50, "output_tokens": 8}},
        ],
    }
    agentcore = FakeAgentCore(json.dumps(state).encode(), content_type="application/json")
    client_ = AgentCoreClient(agentcore, RUNTIME_ARN)

    result = client_.invoke("s" * 36, {"message": "Where are you?", "sessionId": "s" * 36})

    assert agentcore.calls[0]["payload"]["stream"] is False
    assert (result.text, result.model, result.input_tokens, result.output_tokens) == ("We are in Makati City.", "nova-lite", 50, 8)


def test_agentcore_throttling_and_failures_map_to_429_and_503(client, use_agent):
    throttled = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "InvokeAgentRuntime")
    use_agent(AgentCoreClient(FakeAgentCore(b"", error=throttled), RUNTIME_ARN))
    assert client.post(STREAM, json={"content": "Hi"}).status_code == 429

    denied = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "InvokeAgentRuntime")
    use_agent(AgentCoreClient(FakeAgentCore(b"", error=denied), RUNTIME_ARN))
    response = client.post(API, json={"content": "Hi"})
    assert response.status_code == 503 and "unavailable" in response.get_json()["message"]


def test_stream_parser_ignores_noise_and_strips_thinking_markup():
    lines = [b": heartbeat", b"event: message", b'data: {"delta": "<thinking>hmm</thinking>Hello"}', b"data: [DONE]"]
    stream = stream_sse(lines)
    pieces = []
    while True:
        try:
            pieces.append(next(stream))
        except StopIteration as stop:
            result = stop.value
            break
    assert pieces == ["<thinking>hmm</thinking>Hello"] and result.text == "Hello"


def test_visitors_behind_dezr_ui_are_counted_separately(app, client, use_agent):
    # dezr-ui's server holds the key and appends the visitor's address to X-Forwarded-For.
    app.config.update(AI_API_KEY="k" * 40, AI_RATE_LIMIT_PER_MINUTE=1)
    use_agent(ScriptedAgent(reply("a"), reply("b"), reply("c")))
    key = {"Authorization": f"Bearer {'k' * 40}"}

    def ask(visitor):
        return client.post(API, json={"content": "Hi"}, headers={**key, "X-Forwarded-For": visitor})

    assert ask("198.51.100.1").status_code == 201
    assert ask("198.51.100.2").status_code == 201  # another visitor
    assert ask("10.0.0.1, 198.51.100.1").status_code == 429  # the first one again (last entry counts)


def test_without_the_key_forwarded_for_is_ignored(app, client, use_agent):
    app.config["AI_RATE_LIMIT_PER_MINUTE"] = 1
    use_agent(ScriptedAgent(reply("a"), reply("b")))
    assert client.post(API, json={"content": "Hi"}, headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 201
    # A made-up address doesn't dodge the limit.
    assert client.post(API, json={"content": "Hi"}, headers={"X-Forwarded-For": "198.51.100.2"}).status_code == 429


# ---------- The agent failing (BedrockAgentCoreApp's error event) ----------

# What the bedrock_agentcore SDK streams when the agent raises, e.g. no AWS credentials for Bedrock.
SDK_ERROR = {
    "error": "Unable to locate credentials",
    "error_type": "NoCredentialsError",
    "message": "An error occurred during streaming",
}


def test_an_error_event_before_any_answer_is_a_503_and_stores_nothing(app, client, use_agent):
    use_agent(AgentCoreClient(FakeAgentCore(sse(SDK_ERROR)), RUNTIME_ARN))

    response = client.post(STREAM, json={"content": "Hello"})

    assert response.status_code == 503
    assert "An error occurred during streaming" not in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.query(AiMessage).count() == 0


def test_an_error_event_mid_answer_ends_the_stream_with_an_error_event(app, client, use_agent):
    use_agent(AgentCoreClient(FakeAgentCore(sse({"delta": "Our fees "}, SDK_ERROR)), RUNTIME_ARN))

    parsed = events(client.post(STREAM, json={"content": "Hello"}))

    assert parsed[0] == ("delta", {"text": "Our fees "})
    assert parsed[-1][0] == "error" and parsed[-1][1]["code"] == 503
    assert not any(name == "done" for name, _ in parsed)
    with app.app_context():
        assert db.session.query(AiMessage).count() == 0


def test_an_error_json_body_is_not_an_answer(client, use_agent):
    agentcore = FakeAgentCore(json.dumps(SDK_ERROR).encode(), content_type="application/json")
    use_agent(AgentCoreClient(agentcore, RUNTIME_ARN))
    assert client.post(API, json={"content": "Hello"}).status_code == 503
