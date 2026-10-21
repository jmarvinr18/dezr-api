from app.routes.reference_data import blp as reference_data_blp
from app.routes.site_settings import blp as site_settings_blp
from app.routes.pages import blp as pages_blp
from app.routes.search import blp as search_blp
from app.routes.media import blp as media_blp
from app.routes.contact_messages import blp as contact_messages_blp
from app.routes.document_search import blp as document_search_blp
from app.routes.ai_chat import blp as ai_chat_blp
from app.routes.admin_auth import blp as admin_auth_blp
from app.routes.admin_site_settings import blp as admin_site_settings_blp
from app.routes.admin_pages import blp as admin_pages_blp
from app.routes.admin_media import blp as admin_media_blp
from app.routes.admin_contact_messages import blp as admin_contact_messages_blp
from app.routes.admin_documents import blp as admin_documents_blp

blueprints = [
    # Public: what the website reads (and the contact form).
    reference_data_blp,
    site_settings_blp,
    pages_blp,
    search_blp,
    media_blp,
    contact_messages_blp,
    # The AI assistant's retrieval: needs "Authorization: Bearer <DOCUMENT_SEARCH_API_KEY>".
    document_search_blp,
    # The AI assistant's chat, relayed to the dezr-ai agent (Bearer AI_API_KEY when it is set).
    ai_chat_blp,
    # Admin area: sign-in, then everything needs "Authorization: Bearer <token>".
    admin_auth_blp,
    admin_site_settings_blp,
    admin_pages_blp,
    admin_media_blp,
    admin_contact_messages_blp,
    admin_documents_blp,
]
