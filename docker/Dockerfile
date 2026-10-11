# syntax=docker/dockerfile:1
#
# Build context is the repository root:
#   docker build -f docker/Dockerfile -t pet-plant-server .
# Requires a committed uv.lock (run `uv lock` first).
#
# One image, two processes:
#   web    -> default CMD (uvicorn main_web:app)
#   worker -> override the command: python src/main_worker.py <job>

# ---------- builder: resolve and install dependencies ----------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Cached until pyproject.toml / uv.lock change.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------- runtime ----------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src"

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app src ./src
# Committed default configuration read by core.config (see .env.example header).
COPY --chown=app:app .env.example ./
# Pipeline schedule / failure policy read by orchestrator.config.
COPY --chown=app:app config ./config
# Alembic database migrations
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app alembic.ini ./alembic.ini

USER app

EXPOSE 8000

CMD ["uvicorn", "main_web:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
