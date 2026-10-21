"""Create CMS tables: admin_users, admin_login_failures, site_settings, pages, media_files, contact_messages

Revision ID: 0001_cms
Revises:
Create Date: 2026-10-20

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_cms"
down_revision = None
branch_labels = None
depends_on = None

SETTINGS_SECTIONS = ("site", "contact", "nav", "slides", "mission", "banners", "footer", "assistant")
PAGE_SIDEBARS = ("home", "banners")


def _in(column, values):
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade():
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_admin_users"),
        sa.UniqueConstraint("email", name="uq_admin_users_email"),
    )

    op.create_table(
        "admin_login_failures",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=300), nullable=False),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_admin_login_failures"),
    )
    op.create_index("ix_admin_login_failures_key_failed_at", "admin_login_failures", ["key", "failed_at"])

    op.create_table(
        "site_settings",
        sa.Column("section", sa.String(length=30), nullable=False),
        sa.Column("value", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("section", name="pk_site_settings"),
        sa.CheckConstraint(_in("section", SETTINGS_SECTIONS), name="ck_site_settings_section"),
    )

    op.create_table(
        "pages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("heading", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("lang", sa.String(length=10), nullable=False),
        sa.Column("slideshow", sa.Boolean(), nullable=False),
        sa.Column("sidebar", sa.String(length=10), nullable=False),
        sa.Column("tools", sa.Boolean(), nullable=False),
        sa.Column("parent_title", sa.String(length=120), nullable=True),
        sa.Column("parent_to", sa.String(length=500), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("published", sa.Boolean(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("shipped_path", sa.String(length=200), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pages"),
        sa.UniqueConstraint("path", name="uq_pages_path"),
        sa.UniqueConstraint("shipped_path", name="uq_pages_shipped_path"),
        sa.CheckConstraint(_in("sidebar", PAGE_SIDEBARS), name="ck_pages_sidebar"),
    )

    op.create_table(
        "media_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_by", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_media_files"),
        sa.UniqueConstraint("name", name="uq_media_files_name"),
    )

    op.create_table(
        "contact_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("send_copy", sa.Boolean(), nullable=False),
        sa.Column("client_ip_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_contact_messages"),
    )
    op.create_index("ix_contact_messages_created_at", "contact_messages", ["created_at"])
    op.create_index(
        "ix_contact_messages_client_ip_hash_created_at", "contact_messages", ["client_ip_hash", "created_at"]
    )


def downgrade():
    op.drop_index("ix_contact_messages_client_ip_hash_created_at", table_name="contact_messages")
    op.drop_index("ix_contact_messages_created_at", table_name="contact_messages")
    op.drop_table("contact_messages")
    op.drop_table("media_files")
    op.drop_table("pages")
    op.drop_table("site_settings")
    op.drop_index("ix_admin_login_failures_key_failed_at", table_name="admin_login_failures")
    op.drop_table("admin_login_failures")
    op.drop_table("admin_users")
