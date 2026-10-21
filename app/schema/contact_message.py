from marshmallow import Schema, fields, validate

from app.constants import (
    CONTACT_EMAIL_MAX_LENGTH,
    CONTACT_MESSAGE_MAX_LENGTH,
    CONTACT_NAME_MAX_LENGTH,
    CONTACT_SUBJECT_MAX_LENGTH,
)
from app.schema.fields import Text, UtcDateTime


class ContactMessageInputSchema(Schema):
    """The Contact page's form (same fields as dezr-ui's ContactSection.vue sends)."""

    name = Text(CONTACT_NAME_MAX_LENGTH, required=True)
    email = fields.Email(required=True, validate=validate.Length(max=CONTACT_EMAIL_MAX_LENGTH))
    subject = Text(CONTACT_SUBJECT_MAX_LENGTH, required=True)
    message = Text(CONTACT_MESSAGE_MAX_LENGTH, required=True)
    send_copy = fields.Bool(data_key="copy", load_default=False, metadata={"description": "Send me a copy"})


class ContactReceiptSchema(Schema):
    id = fields.UUID()
    received_at = UtcDateTime(attribute="created_at", data_key="receivedAt")


class ContactMessageSchema(Schema):
    id = fields.UUID()
    name = fields.Str()
    email = fields.Str()
    subject = fields.Str()
    message = fields.Str()
    send_copy = fields.Bool(data_key="copy")
    received_at = UtcDateTime(attribute="created_at", data_key="receivedAt")


class PaginationQuerySchema(Schema):
    page = fields.Int(load_default=1, validate=validate.Range(min=1))
    page_size = fields.Int(data_key="pageSize", load_default=20, validate=validate.Range(min=1, max=100))


class ContactMessageListSchema(Schema):
    items = fields.List(fields.Nested(ContactMessageSchema))
    total = fields.Int()
    page = fields.Int()
    page_size = fields.Int(data_key="pageSize")
    page_count = fields.Int(data_key="pageCount")
