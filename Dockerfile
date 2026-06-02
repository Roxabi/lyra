FROM python:3.12.10-slim AS builder

# Install system deps (git needed for GitHub-sourced Python deps)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install dependencies. src/ must be present before `uv sync` — factory is
# installed editable, and uv only links src files that exist at sync time.
# Copying src/ after would leave the editable install pointing at an empty
# dist-info (ImportError: No module named 'factory' at runtime).
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY src/ src/
RUN uv sync --frozen --no-dev

# ── Slim service runtime (hub, telegram, discord) ───────────────────────────
# TODO: pin base-svc by digest — track alongside base:latest pinning issue
FROM ghcr.io/roxabi/base-svc:latest AS svc-runtime

USER root

# UID 1500 pinned per ADR-053 (Quadlet container UID stability)
RUN useradd -u 1500 -m factory

COPY --from=builder --chown=factory:factory /app /app

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH"

USER factory

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD factory config validate || exit 1

# ── Agent runtime (clipool — full gh_token tooling) ───────────────────────────
FROM ghcr.io/roxabi/base:latest AS agent-runtime

USER root

# socat — required by the gh_token shim scripts (git-credential-factory-gh + factory-gh)
# to dial the dispenser Unix socket. Smallest dep that handles UNIX-CONNECT cleanly;
# BSD nc -U fallback in the shims is for hosts where socat is unavailable.
RUN apt-get update \
 && apt-get install -y --no-install-recommends socat \
 && rm -rf /var/lib/apt/lists/*

# UID 1500 pinned per ADR-053 (Quadlet container UID stability)
RUN useradd -u 1500 -m factory \
 && mkdir -p /home/factory/projects \
              /home/factory/.claude/projects \
              /home/factory/.claude/plugins \
              /home/factory/.claude/skills \
              /home/factory/.claude/shared \
              /home/factory/.claude/.git

COPY --from=builder --chown=factory:factory /app /app

# ── factory-gh helper user (#1078) ─────────────────────────────────────────────
# uid 1501 ≠ 1500 (factory's uid) so the token cache file
# /run/factory-gh-token/token.json (mode 0600 owned by 1501) is unreadable from
# inside the Claude subprocess. Both users share group factory-tokenuser
# (gid 1502) so the credential helper + factory-gh shim can connect to the
# dispenser socket (mode 0660 group rw).
RUN groupadd -g 1502 factory-tokenuser \
 && useradd -u 1501 -M -d /nonexistent -s /usr/sbin/nologin -G factory-tokenuser factory-gh \
 && usermod -aG factory-tokenuser factory

# ── gh_token tools (#1078) ───────────────────────────────────────────────────
# Copy helper + dispenser Python modules, and shell shims when T6/T7 land.
# The conditional chmod/ln blocks are no-ops before those tasks ship.
RUN mkdir -p /opt/factory-gh /etc/factory
COPY --chown=root:root src/factory/tools/gh_token/ /opt/factory-gh/
RUN chmod 0755 /opt/factory-gh/*.py 2>/dev/null || true \
 && { [ -f /opt/factory-gh/git-credential-factory-gh ] && chmod 0755 /opt/factory-gh/git-credential-factory-gh || true; } \
 && { [ -f /opt/factory-gh/factory-gh ] && chmod 0755 /opt/factory-gh/factory-gh && ln -sf /opt/factory-gh/factory-gh /usr/local/bin/factory-gh && ln -sf /opt/factory-gh/factory-gh /usr/local/bin/gh || true; }
COPY --chown=root:root deploy/factory-gh/git.config.tmpl /etc/factory/git.config.tmpl
COPY --chown=root:root deploy/factory-gh/hooks/ /opt/factory-gh/hooks/
RUN chmod 0755 /opt/factory-gh/hooks/prepare-commit-msg

# Take `gh` off PATH (AC#5 from #1078): the base image ships /usr/bin/gh which
# would let any process — including the Claude subprocess — invoke gh directly
# and inherit the token if one ever leaked into env. Move it to a non-PATH
# location and point FACTORY_GH_BIN at it so the factory-gh shim still finds it
# without anyone else's `command -v gh` succeeding. /usr/local/bin/gh is a
# shim alias (→ factory-gh) so callers that hardcode `gh` also route through the
# dispenser; the shim's FACTORY_GH_BIN-first resolution guards against recursion.
RUN test -x /usr/bin/gh \
 && mv /usr/bin/gh /opt/factory-gh/gh \
 && chmod 0755 /opt/factory-gh/gh \
 || true
ENV FACTORY_GH_BIN=/opt/factory-gh/gh

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH"

USER factory

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD factory config validate || exit 1
