from flask.views import MethodView
from flask_smorest import Blueprint

from app import constants
from app.schema import ReferenceDataSchema

blp = Blueprint(
    "reference_data",
    __name__,
    url_prefix="/api/v1",
    description="Lookup values for the admin forms (slide scenes, banner styles, upload rules)",
)


@blp.route("/reference-data")
class ReferenceData(MethodView):
    @blp.response(200, ReferenceDataSchema)
    def get(self):
        """Options and limits the admin forms need"""
        return {
            "settings_sections": constants.SETTINGS_SECTIONS,
            "slide_scenes": constants.SLIDE_SCENES,
            "banner_variants": constants.BANNER_VARIANTS,
            "page_sidebars": constants.PAGE_SIDEBARS,
            "page_body_max_length": constants.PAGE_BODY_MAX_LENGTH,
            "media": {
                "accepted_extensions": constants.MEDIA_EXTENSIONS,
                "max_file_size": constants.MEDIA_MAX_FILE_SIZE,
            },
            "documents": {
                "accepted_extensions": list(constants.DOCUMENT_KINDS),
                "max_file_size": constants.DOCUMENT_MAX_FILE_SIZE,
                "title_max_length": constants.DOCUMENT_TITLE_MAX_LENGTH,
            },
        }
