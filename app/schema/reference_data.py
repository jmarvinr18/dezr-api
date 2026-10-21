from marshmallow import Schema, fields


class MediaRulesSchema(Schema):
    accepted_extensions = fields.List(fields.Str(), data_key="acceptedExtensions")
    max_file_size = fields.Int(data_key="maxFileSize")


class DocumentRulesSchema(Schema):
    accepted_extensions = fields.List(fields.Str(), data_key="acceptedExtensions")
    max_file_size = fields.Int(data_key="maxFileSize")
    title_max_length = fields.Int(data_key="titleMaxLength")


class ReferenceDataSchema(Schema):
    settings_sections = fields.List(fields.Str(), data_key="settingsSections")
    slide_scenes = fields.List(fields.Str(), data_key="slideScenes")
    banner_variants = fields.List(fields.Str(), data_key="bannerVariants")
    page_sidebars = fields.List(fields.Str(), data_key="pageSidebars")
    page_body_max_length = fields.Int(data_key="pageBodyMaxLength")
    media = fields.Nested(MediaRulesSchema)
    documents = fields.Nested(DocumentRulesSchema)
