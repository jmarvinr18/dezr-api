from flask.views import MethodView

from app.routes.admin_common import ADMIN_SECURITY, admin_blueprint
from app.schema import ContactMessageListSchema, ContactMessageSchema, PaginationQuerySchema
from app.services import contact_service

blp = admin_blueprint(
    "admin_contact_messages",
    __name__,
    url_prefix="/api/v1/admin/contact-messages",
    description="Admin > Messages: enquiries sent with the Contact page's form",
)


@blp.route("")
class AdminContactMessageList(MethodView):
    @blp.doc(security=ADMIN_SECURITY)
    @blp.arguments(PaginationQuerySchema, location="query")
    @blp.response(200, ContactMessageListSchema)
    def get(self, args):
        """List enquiries, newest first"""
        return contact_service.list_messages(args)


@blp.route("/<uuid:message_id>")
class AdminContactMessageDetail(MethodView):
    @blp.doc(security=ADMIN_SECURITY)
    @blp.response(200, ContactMessageSchema)
    def get(self, message_id):
        """Get one enquiry"""
        return contact_service.get_message(message_id)

    @blp.doc(security=ADMIN_SECURITY)
    @blp.response(204)
    def delete(self, message_id):
        """Delete an enquiry"""
        contact_service.delete_message(contact_service.get_message(message_id))
