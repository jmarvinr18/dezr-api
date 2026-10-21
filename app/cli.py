import click
from flask.cli import AppGroup
from sqlalchemy import select

from app.database import db
from app.model import AdminUser
from app.services.errors import ServiceError


def register_cli(app):
    @app.cli.command("seed-content")
    @click.option("--force", is_flag=True, help="Also reset pages an admin has edited to their shipped version.")
    def seed_content(force):
        """Load the shipped pages (app/seed/pages) into the database.

        Safe to run on every deploy: missing pages are created and unedited pages are brought
        up to date; pages an admin edited are kept unless --force.
        """
        from app.services import page_service

        report = page_service.seed_pages(force=force)
        for path in report.created:
            click.echo(f"Created: {path}")
        for path in report.updated:
            click.echo(f"Updated: {path}")
        for path in report.kept:
            click.echo(f"Kept (edited by an admin): {path}")
        for path in report.blocked:
            click.echo(f"Skipped (path used by another page): {path}")
        click.echo(
            f"{len(report.created)} created, {len(report.updated)} updated, "
            f"{len(report.kept)} kept, {len(report.blocked)} skipped."
        )

    admin = AppGroup("admin", help="Admin accounts for the admin area.")
    app.cli.add_command(admin)

    def _auth():
        from app.extensions.auth import get_auth_service

        return get_auth_service()

    @admin.command("create")
    @click.argument("email")
    @click.password_option(help="12+ characters. Asked for (hidden) when left out.")
    def admin_create(email, password):
        """Create an admin account."""
        try:
            user = _auth().create_admin(email, password)
        except ServiceError as exc:
            raise click.ClickException(exc.message) from exc
        click.echo(f"Created admin {user.email}")

    @admin.command("ensure-default")
    @click.option("--email", default=None, help="Default: DEFAULT_ADMIN_EMAIL (admin@deszr.com).")
    @click.option("--password", default=None, help="Default: DEFAULT_ADMIN_PASSWORD, or a generated one.")
    def admin_ensure_default(email, password):
        """Create the default admin, if there is no admin account yet.

        Safe to run on every start (the Docker image does): once any admin exists it does
        nothing, so it never resets a password. Without a password, a strong one is generated
        and printed once.
        """
        import secrets

        email = app.config["DEFAULT_ADMIN_EMAIL"] if email is None else email
        password = app.config["DEFAULT_ADMIN_PASSWORD"] if password is None else password
        if not email.strip():
            click.echo("No default admin email set (DEFAULT_ADMIN_EMAIL); nothing to do.")
            return
        if db.session.scalar(select(AdminUser.id).limit(1)) is not None:
            click.echo("An admin account exists already; the default admin is not created.")
            return
        generated = not password
        if generated:
            password = secrets.token_urlsafe(15)
        try:
            user = _auth().create_admin(email, password)
        except ServiceError as exc:
            raise click.ClickException(f"The default admin could not be created: {exc.message}") from exc
        click.echo(f"Created the default admin: {user.email}")
        if generated:
            click.echo(f"Password: {password}")
            click.echo(f"It is shown only this once. Change it with `flask admin set-password {user.email}`.")

    @admin.command("set-password")
    @click.argument("email")
    @click.password_option(help="12+ characters. Asked for (hidden) when left out.")
    def admin_set_password(email, password):
        """Set a new password (signs the admin out everywhere)."""
        auth = _auth()
        user = auth.find(email)
        if user is None:
            raise click.ClickException(f"No admin with the email {email}.")
        try:
            auth.set_password(user, password)
        except ServiceError as exc:
            raise click.ClickException(exc.message) from exc
        click.echo(f"Password changed for {user.email}")

    @admin.command("deactivate")
    @click.argument("email")
    def admin_deactivate(email):
        """Stop an admin from signing in (their tokens stop working at once)."""
        user = _auth().find(email)
        if user is None:
            raise click.ClickException(f"No admin with the email {email}.")
        user.is_active = False
        user.session_version += 1
        db.session.commit()
        click.echo(f"Deactivated {user.email}")

    @admin.command("activate")
    @click.argument("email")
    def admin_activate(email):
        """Let a deactivated admin sign in again."""
        user = _auth().find(email)
        if user is None:
            raise click.ClickException(f"No admin with the email {email}.")
        user.is_active = True
        db.session.commit()
        click.echo(f"Activated {user.email}")

    documents = AppGroup("documents", help="Documents the AI assistant can search (RAG ingestion).")
    app.cli.add_command(documents)

    def _ingestion():
        from app.extensions.ingestion import get_ingestion_service

        return get_ingestion_service()

    @documents.command("process-pending")
    @click.option("--limit", type=int, default=None, help="Process at most this many.")
    def documents_process_pending(limit):
        """Process waiting documents once, then exit (for cron, or INGESTION_MODE=worker without a worker)."""
        count = _ingestion().run_pending(limit)
        click.echo(f"Processed {count} document(s).")

    @documents.command("worker")
    def documents_worker():
        """Process documents as they arrive, until stopped (INGESTION_MODE=worker)."""
        import signal
        import time

        stopping = []
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stopping.append(True))
        poll = app.config["INGESTION_POLL_SECONDS"]
        click.echo(f"Document worker started; checking every {poll:g}s.")
        while not stopping:
            try:
                processed = _ingestion().run_pending(limit=1)
            except Exception:
                app.logger.exception("Document worker iteration failed")
                db.session.rollback()
                processed = 0
            finally:
                db.session.remove()
            if not processed:
                # Sleep in short steps so a stop request is noticed quickly.
                for _ in range(max(1, int(poll * 10))):
                    if stopping:
                        break
                    time.sleep(0.1)
        click.echo("Document worker stopped.")

    @documents.command("reprocess")
    @click.argument("document_id", type=click.UUID, required=False)
    @click.option("--all", "everything", is_flag=True, help="Every document (e.g. after changing the chunking or the embedding model).")
    @click.option("--failed", is_flag=True, help="Every failed document.")
    def documents_reprocess(document_id, everything, failed):
        """Chunk and embed documents again."""
        from app.model import Document

        if not (document_id or everything or failed):
            raise click.ClickException("Give a document id, --all or --failed.")
        query = select(Document)
        if document_id:
            query = query.where(Document.id == document_id)
        elif failed:
            query = query.where(Document.status == "failed")
        service = _ingestion()
        rows = list(db.session.scalars(query))
        for document in rows:
            service.requeue(document)
        click.echo(f"Queued {len(rows)} document(s). They are processed by the worker, or run `flask documents process-pending`.")

    @documents.command("list")
    def documents_list():
        """Show documents and their status."""
        from app.model import Document

        for document in db.session.scalars(select(Document).order_by(Document.uploaded_at.desc())):
            chunks = document.chunk_count if document.chunk_count is not None else "-"
            click.echo(f"{document.id}  {document.status:<10} {chunks!s:>5} passages  {document.title}")
            if document.error:
                click.echo(f"    {document.error}")

    @admin.command("list")
    def admin_list():
        """Show the admin accounts."""
        for user in db.session.scalars(select(AdminUser).order_by(AdminUser.email)):
            state = "active" if user.is_active else "inactive"
            last = user.last_login_at.isoformat(timespec="minutes") if user.last_login_at else "never"
            click.echo(f"{user.email:<40} {state:<9} last sign-in: {last}")
