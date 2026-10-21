from flask import send_file
from flask.views import MethodView
from flask_smorest import Blueprint

from app.extensions.storage import get_media_service

blp = Blueprint(
    "media",
    __name__,
    url_prefix="/api/v1/media",
    description="Uploaded images and PDFs, served to the public site",
)


@blp.route("/<string:name>")
class MediaFileContent(MethodView):
    @blp.response(200, content_type="application/octet-stream")
    @blp.alt_response(404, description="No such file")
    def get(self, name):
        """Get an uploaded file (partner photos, page images, banner PDFs)

        Streamed through the API so the bucket stays private. Nothing in a file may run scripts
        or load other resources.
        """
        media = get_media_service()
        file = media.get(name)
        response = send_file(
            media.open(file),
            mimetype=file.content_type,
            download_name=file.name,
            as_attachment=False,
            max_age=300,
        )
        response.headers["Content-Length"] = str(file.size_bytes)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
        )
        return response
