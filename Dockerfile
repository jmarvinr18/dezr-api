# syntax=docker/dockerfile:1

# ---------- Build stage: resolve dependencies into a virtualenv ----------
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first so code changes don't invalidate this layer.
# Only the runtime dependencies are installed (no dev group).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# ---------- Runtime stage ----------
FROM python:3.13-slim AS runtime

# Threaded workers: a media download streams from S3 for as long as the visitor's connection
# takes, and shouldn't hold up /healthz or page requests meanwhile.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    FLASK_APP=app \
    UPLOAD_FOLDER=/data/uploads \
    RUN_MIGRATIONS=1 \
    SEED_CONTENT=1 \
    CREATE_DEFAULT_ADMIN=1 \
    GUNICORN_CMD_ARGS="--bind=0.0.0.0:8000 --workers=3 --threads=8 --worker-class=gthread --timeout=60 --access-logfile=-"

RUN groupadd --system app && useradd --system --gid app --create-home --home-dir /home/app app \
    && mkdir -p /data/uploads && chown -R app:app /data

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY app ./app
COPY migrations ./migrations
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

USER app
VOLUME ["/data/uploads"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["gunicorn", "app:create_app()"]
