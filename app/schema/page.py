from marshmallow import Schema, fields, validate

from app.constants import LANG_PATTERN, PAGE_BODY_MAX_LENGTH, PAGE_PATH_PATTERN, PAGE_SIDEBARS
from app.schema.fields import Href, Text, UtcDateTime

PATH_RULE = validate.Regexp(PAGE_PATH_PATTERN, error="Use lower-case words joined by hyphens, e.g. /about-us.")
LANG_RULE = validate.Regexp(LANG_PATTERN, error="Use a language code such as en or ja.")
ORDER_RULE = validate.Range(min=0, max=10_000)
BODY_RULE = validate.Length(max=PAGE_BODY_MAX_LENGTH)


class PageParentSchema(Schema):
    """An extra breadcrumb between Home and the page (e.g. "About us")."""

    title = Text(120, required=True)
    to = Href(allow_none=True, load_default=None)


class PageSummarySchema(Schema):
    """A published page, as listed in the site map."""

    path = fields.Str()
    title = fields.Str()
    lang = fields.Str()
    order = fields.Int(attribute="sort_order")
    parent = fields.Nested(PageParentSchema, allow_none=True)


class PageSchema(PageSummarySchema):
    """A published page: its layout options and Markdown body."""

    heading = fields.Str(metadata={"description": "Shown above the article; empty hides it"})
    description = fields.Str(metadata={"description": "SEO description"})
    slideshow = fields.Bool(metadata={"description": "Show the practice-area slideshow"})
    sidebar = fields.Str(metadata={"description": "home = mission box + banners, banners = banners only"})
    tools = fields.Bool(metadata={"description": "Print / email icons above the article"})
    body = fields.Str(
        metadata={
            "description": "Markdown with the site's components (::person-profile, ::library-grid, ...). "
            "Not sanitised: render it with dezr-ui's renderMarkdown(), which strips unsafe HTML and links."
        }
    )
    updated_at = UtcDateTime(data_key="updatedAt")


class PageByPathQuerySchema(Schema):
    path = fields.Str(required=True, validate=validate.Length(max=200), metadata={"description": "e.g. / or /contact"})


class PageContentSchema(Schema):
    """The editable fields of a page."""

    path = fields.Str()
    title = fields.Str()
    heading = fields.Str()
    description = fields.Str()
    lang = fields.Str()
    slideshow = fields.Bool()
    sidebar = fields.Str()
    tools = fields.Bool()
    parent = fields.Nested(PageParentSchema, allow_none=True)
    order = fields.Int(attribute="sort_order")
    published = fields.Bool()
    body = fields.Str()


class AdminPageListItemSchema(Schema):
    id = fields.UUID()
    path = fields.Str()
    title = fields.Str()
    order = fields.Int(attribute="sort_order")
    published = fields.Bool()
    edited = fields.Bool(metadata={"description": "Changed since it was shipped (or created by an admin)"})
    has_default = fields.Function(
        lambda page: page.shipped_path is not None,
        data_key="hasDefault",
        metadata={"description": "Can be reset to the shipped version"},
    )
    updated_at = UtcDateTime(data_key="updatedAt")
    updated_by = fields.Str(data_key="updatedBy", allow_none=True)


class AdminPageSchema(AdminPageListItemSchema, PageContentSchema):
    created_at = UtcDateTime(data_key="createdAt")


class AdminPageDetailSchema(AdminPageSchema):
    default = fields.Nested(
        PageContentSchema, allow_none=True, metadata={"description": "The shipped version; null for pages admins created"}
    )


class PageCreateSchema(Schema):
    path = fields.Str(required=True, validate=PATH_RULE)
    title = Text(120, required=True)
    heading = Text(120, blank=True, load_default="")
    description = Text(300, blank=True, load_default="")
    lang = fields.Str(load_default="en", validate=LANG_RULE)
    slideshow = fields.Bool(load_default=False)
    sidebar = fields.Str(load_default="banners", validate=validate.OneOf(PAGE_SIDEBARS))
    tools = fields.Bool(load_default=True)
    parent = fields.Nested(PageParentSchema, allow_none=True, load_default=None)
    order = fields.Int(attribute="sort_order", load_default=99, validate=ORDER_RULE)
    published = fields.Bool(load_default=True)
    body = fields.Str(load_default="", validate=BODY_RULE)


class PageUpdateSchema(Schema):
    """Only the fields that are sent change."""

    path = fields.Str(validate=PATH_RULE)
    title = Text(120)
    heading = Text(120, blank=True)
    description = Text(300, blank=True)
    lang = fields.Str(validate=LANG_RULE)
    slideshow = fields.Bool()
    sidebar = fields.Str(validate=validate.OneOf(PAGE_SIDEBARS))
    tools = fields.Bool()
    parent = fields.Nested(PageParentSchema, allow_none=True)
    order = fields.Int(attribute="sort_order", validate=ORDER_RULE)
    published = fields.Bool()
    body = fields.Str(validate=BODY_RULE)
