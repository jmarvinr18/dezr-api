from datetime import timezone

from marshmallow import fields, validate

from app.constants import HREF_PATTERN


class UtcDateTime(fields.DateTime):
    """Always dumps with a UTC offset, even when the database hands back a naive value."""

    def _serialize(self, value, attr, obj, **kwargs):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return super()._serialize(value, attr, obj, **kwargs)


class Text(fields.String):
    """A string trimmed of surrounding whitespace and at most `max_length` long.

    Blank ("" or only spaces) is refused unless blank=True, the same as text() / optionalText()
    in dezr-ui/shared/site/schema.ts.
    """

    def __init__(self, max_length: int, *, blank: bool = False, **kwargs):
        validators = [validate.Length(max=max_length)]
        if not blank:
            validators.insert(0, validate.Length(min=1, error="Required."))
        extra = kwargs.pop("validate", None)
        if extra:
            validators.extend(extra if isinstance(extra, list) else [extra])
        super().__init__(validate=validators, **kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        return super()._deserialize(value, attr, data, **kwargs).strip()


class Href(Text):
    """A site path ("/contact") or an https / mailto / tel link, never javascript: and friends."""

    def __init__(self, max_length: int = 500, **kwargs):
        super().__init__(
            max_length,
            validate=validate.Regexp(HREF_PATTERN, error="Use a site path (/page) or a full https:// link."),
            **kwargs,
        )
