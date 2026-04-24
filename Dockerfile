FROM python:3.12.10-slim AS builder

# Install system deps (git needed for GitHub-sourced Python deps)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.11.4 /uv /uvx /usr/local/bin/

WORKDIR /app

# Install dependencies. src/ must be present before `uv sync` — lyra is
# installed editable, and uv only links src files that exist at sync time.
# Copying src/ after would leave the editable install pointing at an empty
# dist-info (ImportError: No module named 'lyra' at runtime).
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY src/ src/
RUN uv sync --frozen --no-dev

# ── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.12.10-slim AS runtime

# UID 1500 pinned per ADR-053 (Quadlet container UID stability)
RUN useradd -u 1500 -m lyra

COPY --from=builder --chown=lyra:lyra /app /app

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH"

USER lyra

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD lyra config validate || exit 1
