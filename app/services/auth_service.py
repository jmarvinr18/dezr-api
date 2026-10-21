"""Admin sign-in: accounts in admin_users, signed access tokens, and a lockout after repeated
wrong passwords (per IP and per account), the same rules as dezr-ui's server/utils/adminAuth.ts.

Tokens are signed with SECRET_KEY and carry the admin's id and session_version. Signing out
bumps session_version, which revokes every token the admin holds.
"""

import math
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from app.model import AdminLoginFailure, AdminUser
from app.services.errors import Conflict, NotConfigured, TooManyRequests, Unauthorized, ValidationFailed

TOKEN_SALT = "dezr-admin-token"
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 200


def _now():
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    # SQLite hands back naive datetimes; everything stored is UTC.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


_decoy_hash: str | None = None


def _decoy() -> str:
    """Checked when the email is unknown, so a wrong email takes as long as a wrong password."""
    global _decoy_hash
    if _decoy_hash is None:
        _decoy_hash = generate_password_hash(secrets.token_hex(16))
    return _decoy_hash


@dataclass(frozen=True)
class LoginResult:
    user: AdminUser
    token: str
    expires_at: datetime


class AuthService:
    def __init__(
        self,
        session: Session,
        secret_key: str | None,
        *,
        session_hours: float = 8,
        max_failures: int = 5,
        lockout_minutes: int = 15,
    ):
        self._session = session
        self._secret_key = secret_key
        self._session_hours = session_hours
        self._max_failures = max_failures
        self._lockout = timedelta(minutes=lockout_minutes)

    # ---------- Sign-in ----------

    def login(self, email: str, password: str, client_ip: str | None) -> LoginResult:
        serializer = self._serializer()
        email = normalize_email(email)
        keys = [f"ip:{client_ip or 'unknown'}", f"user:{email}"]
        self._assert_not_locked(keys)

        admin = self._session.scalar(select(AdminUser).where(AdminUser.email == email))
        # Same work whether or not the account exists, so the answer can't reveal which emails are admins.
        ok = check_password_hash(admin.password_hash if admin else _decoy(), password)
        if not (ok and admin and admin.is_active):
            self._record_failure(keys)
            raise Unauthorized("Incorrect email or password.")

        # A success clears the account's count; the IP's stays, so one valid account can't reset it.
        self._session.execute(delete(AdminLoginFailure).where(AdminLoginFailure.key == keys[1]))
        admin.last_login_at = _now()
        self._session.commit()

        token = serializer.dumps({"sub": str(admin.id), "ver": admin.session_version})
        return LoginResult(admin, token, _now() + timedelta(hours=self._session_hours))

    def logout(self, admin: AdminUser) -> None:
        """Revokes every token the admin holds (all devices)."""
        admin.session_version += 1
        self._session.commit()

    def admin_for_token(self, token: str) -> AdminUser:
        serializer = self._serializer()
        try:
            data = serializer.loads(token, max_age=int(self._session_hours * 3600))
            admin_id = uuid.UUID(data["sub"])
            version = int(data["ver"])
        except (BadData, KeyError, TypeError, ValueError):
            raise Unauthorized("Your session has ended. Please sign in again.") from None
        admin = self._session.get(AdminUser, admin_id)
        if admin is None or not admin.is_active or admin.session_version != version:
            raise Unauthorized("Your session has ended. Please sign in again.")
        return admin

    # ---------- Accounts (CLI) ----------

    def create_admin(self, email: str, password: str) -> AdminUser:
        email = normalize_email(email)
        if "@" not in email or len(email) > 255:
            raise ValidationFailed("Enter a valid email address.")
        if self._session.scalar(select(AdminUser.id).where(AdminUser.email == email)):
            raise Conflict(f"An admin with the email {email} already exists.")
        admin = AdminUser(email=email, password_hash=self._hash(password))
        self._session.add(admin)
        self._session.commit()
        return admin

    def set_password(self, admin: AdminUser, password: str) -> None:
        """Sets a new password and signs the admin out everywhere."""
        admin.password_hash = self._hash(password)
        admin.session_version += 1
        self._session.commit()

    def find(self, email: str) -> AdminUser | None:
        return self._session.scalar(select(AdminUser).where(AdminUser.email == normalize_email(email)))

    # ---------- Helpers ----------

    def _serializer(self) -> URLSafeTimedSerializer:
        if not self._secret_key:
            raise NotConfigured("Admin sign-in is not configured on this server (SECRET_KEY).")
        return URLSafeTimedSerializer(self._secret_key, salt=TOKEN_SALT)

    @staticmethod
    def _hash(password: str) -> str:
        if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            raise ValidationFailed(f"Use a password of at least {MIN_PASSWORD_LENGTH} characters.")
        return generate_password_hash(password)

    def _assert_not_locked(self, keys: list[str]) -> None:
        """429 while an IP or an account has too many recent failed sign-ins."""
        now = _now()
        for key in keys:
            failures = self._session.scalars(
                select(AdminLoginFailure.failed_at)
                .where(AdminLoginFailure.key == key, AdminLoginFailure.failed_at > now - self._lockout)
                .order_by(AdminLoginFailure.failed_at)
            ).all()
            if len(failures) >= self._max_failures:
                retry_after = max(1, math.ceil((_as_utc(failures[0]) + self._lockout - now).total_seconds()))
                raise TooManyRequests(
                    f"Too many attempts. Try again in {math.ceil(retry_after / 60)} minutes.", retry_after=retry_after
                )

    def _record_failure(self, keys: list[str]) -> None:
        now = _now()
        # Old failures no longer count; drop them while we're here.
        self._session.execute(
            delete(AdminLoginFailure).where(
                AdminLoginFailure.key.in_(keys), AdminLoginFailure.failed_at <= now - self._lockout
            )
        )
        for key in keys:
            self._session.add(AdminLoginFailure(key=key, failed_at=now))
        self._session.commit()
