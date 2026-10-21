from marshmallow import Schema, fields, validate

from app.schema.fields import UtcDateTime


class LoginInputSchema(Schema):
    email = fields.Str(required=True, validate=validate.Length(min=1, max=255))
    password = fields.Str(required=True, validate=validate.Length(min=1, max=200))


class AdminUserSchema(Schema):
    id = fields.UUID()
    email = fields.Str()
    last_login_at = UtcDateTime(data_key="lastLoginAt", allow_none=True)


class LoginResultSchema(Schema):
    token = fields.Str(metadata={"description": "Send as \"Authorization: Bearer <token>\""})
    expires_at = UtcDateTime(data_key="expiresAt")
    user = fields.Nested(AdminUserSchema)
