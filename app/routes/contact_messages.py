from flask import current_app
from flask.views import MethodView
from flask_smorest import Blueprint

from app.schema import ContactMessageInputSchema, ContactReceiptSchema
from app.services import contact_service
from app.services.client import client_ip

blp = Blueprint(
    "contact_messages",
    __name__,
    url_prefix="/api/v1/contact-messages",
    description="The Contact page's form",
)


@blp.route("")
class ContactMessages(MethodView):
    @blp.arguments(ContactMessageInputSchema)
    @blp.response(201, ContactReceiptSchema)
    @blp.alt_response(429, description="Too many messages from this visitor in the last hour")
    def post(self, data):
        """Send an enquiry to the firm

        Stored for the firm to read in the admin area. Each visitor may send
        CONTACT_RATE_LIMIT_PER_HOUR messages an hour.
        """
        return contact_service.submit(
            data,
            client_ip(),
            secret=current_app.config.get("SECRET_KEY") or "",
            limit_per_hour=current_app.config["CONTACT_RATE_LIMIT_PER_HOUR"],
        )
