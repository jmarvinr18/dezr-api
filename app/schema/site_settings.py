"""Site settings: what the header, menu, slideshow, right column, footer and assistant show.

Mirrors siteSettingsSchema in dezr-ui/shared/site/schema.ts, field for field.
"""

from marshmallow import Schema, fields, validate

from app.constants import BANNER_VARIANTS, LANG_PATTERN, SETTINGS_SECTIONS, SLIDE_SCENES
from app.schema.fields import Href, Text, UtcDateTime


class LinkSchema(Schema):
    label = Text(80, required=True)
    to = Href(required=True)


class LogoSchema(Schema):
    title = Text(60, required=True)
    tagline = Text(80, blank=True, load_default="")


class SiteSectionSchema(Schema):
    name = Text(120, required=True)
    logo = fields.Nested(LogoSchema, required=True)
    description = Text(300, blank=True, load_default="")
    url = fields.Url(
        required=True, schemes={"http", "https"}, require_tld=False, validate=validate.Length(max=300)
    )


class ContactSectionSchema(Schema):
    address_lines = fields.List(
        Text(120), data_key="addressLines", required=True, validate=validate.Length(min=1, max=4)
    )
    email = fields.Email(required=True, validate=validate.Length(max=200))
    japan_desk_email = fields.Email(data_key="japanDeskEmail", required=True, validate=validate.Length(max=200))
    phone = Text(40, blank=True, load_default="")
    fax = Text(40, blank=True, load_default="")
    hours = Text(120, blank=True, load_default="")


class NavItemSchema(Schema):
    label = Text(40, required=True)
    to = Href(required=True)
    # Marks the item drawn as the home icon.
    home = fields.Bool(load_default=False)


class SlideSchema(Schema):
    title = Text(60, required=True)
    caption = Text(160, blank=True, load_default="")
    scene = fields.Str(required=True, validate=validate.OneOf(SLIDE_SCENES))
    headline = Text(30, required=True)
    headline2 = Text(30, blank=True, load_default="")
    tagline = Text(120, blank=True, load_default="")


class MissionSectionSchema(Schema):
    title = Text(60, required=True)
    text = Text(300, blank=True, load_default="")
    link = fields.Nested(LinkSchema, required=True)


class BannerSchema(Schema):
    variant = fields.Str(required=True, validate=validate.OneOf(BANNER_VARIANTS))
    kicker = Text(60, blank=True, load_default="")
    title = Text(60, required=True)
    title2 = Text(60, blank=True, load_default="")
    action = Text(30, blank=True, load_default="")
    url = Href(required=True)
    lang = fields.Str(
        required=True,
        validate=validate.Regexp(LANG_PATTERN, error="Use a language code such as en or ja."),
    )


class FooterColumnSchema(Schema):
    title = Text(60, required=True)
    links = fields.List(fields.Nested(LinkSchema), required=True, validate=validate.Length(max=12))


class FooterSectionSchema(Schema):
    columns = fields.List(fields.Nested(FooterColumnSchema), required=True, validate=validate.Length(max=4))


class AssistantSectionSchema(Schema):
    title = Text(60, required=True)
    name = Text(60, required=True)
    subtitle = Text(200, blank=True, load_default="")
    greeting = Text(80, required=True)
    intro = Text(300, blank=True, load_default="")
    placeholder = Text(80, blank=True, load_default="")
    prompts = fields.List(Text(160), required=True, validate=validate.Length(max=6))
    disclaimer = Text(300, blank=True, load_default="")


class SiteSettingsSchema(Schema):
    """The whole settings document. Every section is required on PUT; PATCH sends only the
    sections it changes (each one is still replaced whole)."""

    site = fields.Nested(SiteSectionSchema, required=True)
    contact = fields.Nested(ContactSectionSchema, required=True)
    nav = fields.List(fields.Nested(NavItemSchema), required=True, validate=validate.Length(min=1, max=12))
    slides = fields.List(fields.Nested(SlideSchema), required=True, validate=validate.Length(min=1, max=12))
    mission = fields.Nested(MissionSectionSchema, required=True)
    banners = fields.List(fields.Nested(BannerSchema), required=True, validate=validate.Length(max=8))
    footer = fields.Nested(FooterSectionSchema, required=True)
    assistant = fields.Nested(AssistantSectionSchema, required=True)


class SiteSettingsPatchSchema(SiteSettingsSchema):
    """Any subset of the sections; a section that is sent is replaced whole."""

    def __init__(self, *args, **kwargs):
        # Only the sections themselves become optional; inside a section every rule still applies.
        kwargs.setdefault("partial", tuple(SETTINGS_SECTIONS))
        super().__init__(*args, **kwargs)


class SettingsRevisionSchema(Schema):
    updated_at = UtcDateTime(data_key="updatedAt")
    updated_by = fields.Str(data_key="updatedBy")


class AdminSiteSettingsSchema(Schema):
    settings = fields.Nested(SiteSettingsSchema, metadata={"description": "What the site shows now"})
    defaults = fields.Nested(SiteSettingsSchema, metadata={"description": "The shipped settings"})
    revision = fields.Nested(
        SettingsRevisionSchema, allow_none=True, metadata={"description": "The latest change; null when unchanged"}
    )
    edited_sections = fields.List(
        fields.Str(), data_key="editedSections", metadata={"description": "Sections that differ from the shipped ones"}
    )
