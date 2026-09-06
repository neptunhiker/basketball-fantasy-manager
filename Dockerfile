# syntax=docker/dockerfile:1

# --- Stage 1: build the stylesheet with the standalone Tailwind CLI ----------
FROM debian:bookworm-slim AS tailwind

ARG TAILWIND_VERSION=v4.1.18
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN ARCH="$(dpkg --print-architecture)" \
    && case "$ARCH" in \
         amd64) TARGET=tailwindcss-linux-x64 ;; \
         arm64) TARGET=tailwindcss-linux-arm64 ;; \
         *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;; \
       esac \
    && curl -sSfL "https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/${TARGET}" \
         -o /usr/local/bin/tailwindcss \
    && chmod +x /usr/local/bin/tailwindcss

# Only the files Tailwind scans for class names.
COPY assets ./assets
COPY templates ./templates
COPY apps ./apps
RUN tailwindcss -i ./assets/input.css -o ./static/css/app.css --minify


# --- Stage 2: python dependencies -------------------------------------------
FROM python:3.13-slim-bookworm AS deps

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
# Cached separately from the source so code changes don't reinstall packages.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project


# --- Stage 3: runtime --------------------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.prod

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app

WORKDIR /app

COPY --from=deps --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app . .
COPY --from=tailwind --chown=app:app /build/static/css/app.css /app/static/css/app.css

# Baked into the image so no shared volume is needed at runtime. The real
# SECRET_KEY and DATABASE_URL arrive as Fly secrets at boot; collectstatic only
# needs settings to import, so placeholders are fine here.
RUN SECRET_KEY=build-only DATABASE_URL=postgres://u:p@localhost/db RESEND_API_KEY=build-only \
    python manage.py collectstatic --noinput --clear

USER app

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8080/healthz || exit 1

CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "2", \
     "--threads", "4", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
