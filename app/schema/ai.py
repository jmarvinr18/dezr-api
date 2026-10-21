"""Schemas for the AI message endpoints. Shapes match dezr-ui/app/types/chat.ts (the same as
vendor-portal-api's, so the chat client works with either)."""

from marshmallow import Schema, fields, validate

from app.constants import AI_MESSAGE_MAX_LENGTH
from app.schema.fields import UtcDateTime

NOT_BLANK = validate.Regexp(r"\s*\S", error="Cannot be blank.")


class AiCitationSchema(Schema):
    type = fields.Str(metadata={"description": "document | policy"})
    id = fields.Str()
    label = fields.Str()


class AiStepSchema(Schema):
    """A tool the agent used while answering (dezr-ai: search_documents)."""

    id = fields.Str()
    name = fields.Str()
    status = fields.Str(metadata={"description": "started | completed | failed"})
    input = fields.Str(allow_none=True)
    output = fields.Str(allow_none=True)


class AiTodoSchema(Schema):
    id = fields.Str()
    content = fields.Str()
    status = fields.Str(metadata={"description": "pending | in_progress | completed"})


class AiMessageSchema(Schema):
    id = fields.UUID()
    role = fields.Str(metadata={"description": "user | assistant"})
    content = fields.Str()
    created_at = UtcDateTime(data_key="createdAt")
    citations = fields.Function(
        lambda m: m.citations or [], metadata={"description": "AiCitation[]: the documents the answer came from"}
    )
    thinking = fields.Function(
        lambda m: m.reasoning,
        metadata={"description": "The agent's reasoning before it answered, when the model shares it"},
    )
    steps = fields.Function(
        lambda m: AiStepSchema(many=True).dump(m.steps or []),
        metadata={"description": "AiStep[]: the tools the agent used"},
    )
    todos = fields.Function(
        lambda m: AiTodoSchema(many=True).dump(m.todos or []),
        metadata={"description": "AiTodo[]: the to-do list the agent planned for the answer"},
    )


class AiContextSchema(Schema):
    page = fields.Str(
        load_default=None, validate=validate.Length(max=200), metadata={"description": "The page the visitor was on"}
    )


class AiMessageCreateSchema(Schema):
    content = fields.Str(required=True, validate=[validate.Length(max=AI_MESSAGE_MAX_LENGTH), NOT_BLANK])
    session_id = fields.UUID(
        data_key="sessionId",
        load_default=None,
        allow_none=True,
        metadata={"description": "Continue this conversation. Omit to start a new one."},
    )
    agent_id = fields.Str(
        data_key="agentId",
        load_default=None,
        allow_none=True,
        validate=validate.Length(max=50),
        metadata={"description": "Which agent to route to (defined by dezr-ai). Ignored with sessionId."},
    )
    context = fields.Nested(
        AiContextSchema,
        load_default=None,
        allow_none=True,
        metadata={"description": "What the visitor was looking at. Ignored with sessionId."},
    )


class AiReplySchema(Schema):
    session_id = fields.UUID(data_key="sessionId")
    user_message = fields.Nested(AiMessageSchema, data_key="userMessage")
    assistant_message = fields.Nested(AiMessageSchema, data_key="assistantMessage")
