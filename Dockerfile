FROM python:3.12.10-slim AS builder

# Install system deps (git needed for GitHub-sourced Python deps)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

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
FROM ghcr.io/roxabi/base:latest AS runtime

USER root

# socat — required by the gh_token shim scripts (git-credential-lyra-gh + lyra-gh)
# to dial the dispenser Unix socket. Smallest dep that handles UNIX-CONNECT cleanly;
# BSD nc -U fallback in the shims is for hosts where socat is unavailable.
RUN apt-get update \
 && apt-get install -y --no-install-recommends socat \
 && rm -rf /var/lib/apt/lists/*

# UID 1500 pinned per ADR-053 (Quadlet container UID stability)
RUN useradd -u 1500 -m lyra \
 && mkdir -p /home/lyra/projects \
              /home/lyra/.claude/projects \
              /home/lyra/.claude/plugins \
              /home/lyra/.claude/skills \
              /home/lyra/.claude/shared \
              /home/lyra/.claude/.git

COPY --from=builder --chown=lyra:lyra /app /app

# ── lyra-gh helper user (#1078) ─────────────────────────────────────────────
# uid 1501 ≠ 1500 (lyra's uid) so the token cache file
# /run/lyra-gh-token/token.json (mode 0600 owned by 1501) is unreadable from
# inside the Claude subprocess. Both users share group lyra-tokenuser
# (gid 1502) so the credential helper + lyra-gh shim can connect to the
# dispenser socket (mode 0660 group rw).
RUN groupadd -g 1502 lyra-tokenuser \
 && useradd -u 1501 -M -d /nonexistent -s /usr/sbin/nologin -G lyra-tokenuser lyra-gh \
 && usermod -aG lyra-tokenuser lyra

# ── gh_token tools (#1078) ───────────────────────────────────────────────────
# Copy helper + dispenser Python modules, and shell shims when T6/T7 land.
# The conditional chmod/ln blocks are no-ops before those tasks ship.
RUN mkdir -p /opt/lyra-gh /etc/lyra
COPY --chown=root:root src/lyra/tools/gh_token/ /opt/lyra-gh/
RUN chmod 0755 /opt/lyra-gh/*.py 2>/dev/null || true \
 && { [ -f /opt/lyra-gh/git-credential-lyra-gh ] && chmod 0755 /opt/lyra-gh/git-credential-lyra-gh || true; } \
 && { [ -f /opt/lyra-gh/lyra-gh ] && chmod 0755 /opt/lyra-gh/lyra-gh && ln -s /opt/lyra-gh/lyra-gh /usr/local/bin/lyra-gh && ln -s /opt/lyra-gh/lyra-gh /usr/local/bin/gh || true; }
COPY --chown=root:root deploy/lyra-gh/git.config.tmpl /etc/lyra/git.config.tmpl

# Take `gh` off PATH (AC#5 from #1078): the base image ships /usr/bin/gh which
# would let any process — including the Claude subprocess — invoke gh directly
# and inherit the token if one ever leaked into env. Move it to a non-PATH
# location and point LYRA_GH_BIN at it so the lyra-gh shim still finds it
# without anyone else's `command -v gh` succeeding.
# /usr/local/bin/gh is a transparent shim alias (→ lyra-gh) — callers that
# hardcode `gh` go through the dispenser automatically; no token recursion
# because the shim resolves via LYRA_GH_BIN before any `command -v gh` fallback.
RUN test -x /usr/bin/gh \
 && mv /usr/bin/gh /opt/lyra-gh/gh \
 && chmod 0755 /opt/lyra-gh/gh \
 || true
ENV LYRA_GH_BIN=/opt/lyra-gh/gh

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH"

USER lyra

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD lyra config validate || exit 1
