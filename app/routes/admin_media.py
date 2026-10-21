from flask import request
from flask.views import MethodView

from app.extensions.storage import get_media_service
from app.routes.admin_common import ADMIN_SECURITY, admin_blueprint
from app.schema import MediaFileSchema
from app.services.current_admin import get_current_admin
from app.services.upload_validator import IncomingFile

blp = admin_blueprint(
    "admin_media",
    __name__,
    url_prefix="/api/v1/admin/media",
    description="Admin > Media: upload and delete images and PDFs (stored in S3)",
)


@blp.route("")
class AdminMediaList(MethodView):
    @blp.doc(security=ADMIN_SECURITY)
    @blp.response(200, MediaFileSchema(many=True))
    def get(self):
        """List uploaded files, newest first"""
        return get_media_service().list_files()

    @blp.doc(security=ADMIN_SECURITY)
    @blp.response(201, MediaFileSchema)
    @blp.alt_response(413, description="Larger than 10 MB")
    @blp.alt_response(422, description="Not a PNG, JPG, GIF, WebP image or PDF; nothing was stored")
    @blp.alt_response(503, description="The media store (S3) is unavailable; nothing was stored")
    def post(self):
        """Upload an image or PDF

        multipart/form-data with one `file` part, up to 10 MB. The type is recognised from the
        file's content, not its name. It is published under a URL-safe version of its name
        ("Partner Photo.JPG" → partner-photo.jpg, then partner-photo-2.jpg, ...).
        """
        upload = request.files.get("file")
        incoming = IncomingFile(upload.filename, upload.stream) if upload and upload.filename else None
        return get_media_service().upload(incoming, get_current_admin().email)


@blp.route("/<string:name>")
class AdminMediaDetail(MethodView):
    @blp.doc(security=ADMIN_SECURITY)
    @blp.response(204)
    def delete(self, name):
        """Delete an uploaded file

        Pages or banners that still link to it will show a broken link.
        """
        media = get_media_service()
        media.remove(media.get(name))
