FROM python:3.12-slim AS builder

# Install system deps (git needed for GitHub-sourced Python deps)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install dependencies (no voice extras — no GPU in Docker)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-extra voice

# Copy source and reinstall in editable mode so the lyra CLI is available
COPY src/ src/
RUN uv sync --frozen --no-dev --no-extra voice

# ── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

RUN useradd -m lyra

COPY --from=builder --chown=lyra:lyra /app /app

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH"

USER lyra

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD lyra config validate || exit 1
