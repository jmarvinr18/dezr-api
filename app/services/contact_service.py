"""Enquiries from the Contact page's form.

Messages are stored for the firm to read in the admin area. Email delivery is not wired up
yet; when it is, send from submit() after the commit so a mail outage never loses an enquiry.
"""

import hashlib
import hmac
import math
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.database import db
from app.model import ContactMessage
from app.services.errors import NotFound, TooManyRequests


def hash_ip(ip: str | None, secret: str) -> str | None:
    """A keyed hash of the visitor's IP, enough to count their messages without storing it."""
    if not ip:
        return None
    return hmac.new(secret.encode(), ip.encode(), hashlib.sha256).hexdigest()


def submit(data: dict, client_ip: str | None, *, secret: str, limit_per_hour: int) -> ContactMessage:
    ip_hash = hash_ip(client_ip, secret)
    if ip_hash and limit_per_hour > 0:
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        recent = db.session.scalar(
            select(func.count())
            .select_from(ContactMessage)
            .where(ContactMessage.client_ip_hash == ip_hash, ContactMessage.created_at > since)
        )
        if recent >= limit_per_hour:
            raise TooManyRequests(
                "You've sent several messages already. Please try again later or email us directly.",
                retry_after=3600,
            )

    message = ContactMessage(client_ip_hash=ip_hash, **data)
    db.session.add(message)
    db.session.commit()
    return message


def list_messages(args: dict) -> dict:
    total = db.session.scalar(select(func.count()).select_from(ContactMessage))
    page_size = args["page_size"]
    page_count = max(1, math.ceil(total / page_size))
    page = min(args["page"], page_count)
    items = db.session.scalars(
        select(ContactMessage)
        .order_by(ContactMessage.created_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size, "page_count": page_count}


def get_message(message_id: uuid.UUID) -> ContactMessage:
    message = db.session.get(ContactMessage, message_id)
    if message is None:
        raise NotFound("Message not found.")
    return message


def delete_message(message: ContactMessage) -> None:
    db.session.delete(message)
    db.session.commit()
