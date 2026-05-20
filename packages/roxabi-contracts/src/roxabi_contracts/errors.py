"""WorkerError envelope + KNOWN_CODES registry.

See docs/architecture/adr/066-unified-worker-error-envelope-nats-reply-contracts.mdx.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "WorkerError",
    "CodeMeta",
    "KNOWN_CODES",
    "scrub_credentials",
    "truncate_with_marker",
]

# Maximum stored length for free-text fields. Long stack traces / framing errors
# are truncated to fit; we never raise a ValidationError on overflow because
# WorkerError construction sites are inside `except` handlers — raising there
# would crash the very error path that exists to surface the problem.
_MESSAGE_MAX = 512
_DETAIL_MAX = 2048
_TRUNC_MARKER = "…"

# Schemes whose URLs may legitimately appear in worker exception strings and
# may carry credentials in the userinfo component.
_CREDENTIAL_SCHEMES = frozenset(
    {
        "nats",
        "nats+tls",
        "amqp",
        "amqps",
        "redis",
        "rediss",
        "http",
        "https",
        "postgres",
        "postgresql",
        "mysql",
    }
)

# Loose URL extractor — matches `scheme://...` up to the first whitespace.
# Resolution of credential boundaries (user / pass / host) is delegated to
# `urllib.parse.urlsplit`, which correctly handles `@` in passwords (it
# anchors the userinfo on the LAST `@` before the host), URL-encoded chars,
# and IPv6 hosts.
_URL_RE = re.compile(r"(?:[a-zA-Z][a-zA-Z0-9+.\-]*)://[^\s\"'<>]+")


def _scrub_url(url: str) -> str:
    """Replace userinfo in `url` with `***:***` if its scheme is in the allowlist.

    Returns the URL unchanged if the scheme is unknown, or if there is no
    userinfo to scrub. Uses `urlsplit` for boundary detection so passwords
    containing `@` are handled correctly (RFC 3986 anchors userinfo on the
    last `@` before the host).
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.scheme.lower() not in _CREDENTIAL_SCHEMES:
        return url
    if "@" not in parts.netloc:
        return url
    # netloc = "user[:pass]@host[:port]" — split on LAST `@`
    _, _, host_port = parts.netloc.rpartition("@")
    new_netloc = f"***:***@{host_port}"
    return urlunsplit(
        (parts.scheme, new_netloc, parts.path, parts.query, parts.fragment)
    )


def scrub_credentials(value: str) -> str:
    """Scrub credentials from any embedded URLs in `value`.

    Replaces the userinfo (``user:pass@``) of every URL whose scheme is in
    the credential-bearing allowlist (``nats``, ``nats+tls``, ``amqp``,
    ``amqps``, ``redis``, ``rediss``, ``http``, ``https``, ``postgres``,
    ``postgresql``, ``mysql``) with ``***:***``. Returns the value
    unchanged if no scrubbing applies. Safe to call on arbitrary free-text
    such as ``str(exc)``.
    """
    return _URL_RE.sub(lambda m: _scrub_url(m.group(0)), value)


def truncate_with_marker(value: str, limit: int) -> str:
    """Truncate `value` to `limit` chars, replacing the tail with `…` on overflow.

    Truncates rather than raises so error-path code never crashes on long
    stack traces. The marker reserves 1 char so the final string fits exactly
    within `limit`.
    """
    if len(value) <= limit:
        return value
    return value[: limit - len(_TRUNC_MARKER)] + _TRUNC_MARKER


class WorkerError(BaseModel):
    """Structured error returned on NATS reply subjects by all Lyra workers."""

    # `code` is a registry key — strict pattern, no normalisation. Newlines,
    # control chars, and uppercase are rejected at construction time so
    # downstream log lines and counter labels are forge-safe.
    code: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9._-]*$")

    # `message` and `detail` are free-text. Scrubbed for credentials and
    # truncated to bounded length. min_length applies to the post-scrub /
    # post-truncate value via the validator below.
    message: str = Field(min_length=1)
    retryable: bool = True
    detail: str | None = Field(default=None)

    model_config = {"extra": "ignore"}

    @field_validator("message")
    @classmethod
    def _sanitize_message(cls, v: str) -> str:
        return truncate_with_marker(scrub_credentials(v), _MESSAGE_MAX)

    @field_validator("detail")
    @classmethod
    def _sanitize_detail(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return truncate_with_marker(scrub_credentials(v), _DETAIL_MAX)


class CodeMeta(BaseModel):
    """Metadata for a registered error code."""

    domain: str
    default_retryable: bool
    description: str


# ---------------------------------------------------------------------------
# KNOWN_CODES — canonical registry (ADR-066 § "The code namespace")
# ---------------------------------------------------------------------------
# Domains: transport | pool | worker | cli | llm | voice | image
# ---------------------------------------------------------------------------

KNOWN_CODES: dict[str, CodeMeta] = {
    # --- transport -----------------------------------------------------------
    "transport.timeout": CodeMeta(
        domain="transport",
        default_retryable=True,
        description="NATS request timed out before a reply was received.",
    ),
    "transport.no_responders": CodeMeta(
        domain="transport",
        default_retryable=True,
        description="NATS returned a no-responders status; no subscriber on the subject.",  # noqa: E501
    ),
    "transport.parse": CodeMeta(
        domain="transport",
        default_retryable=False,
        description="Inbound NATS payload could not be parsed (malformed JSON or schema mismatch).",  # noqa: E501
    ),
    "transport.contract_mismatch": CodeMeta(
        domain="transport",
        default_retryable=False,
        description="CONTRACT_VERSION or schema shape does not match what this consumer expects.",  # noqa: E501
    ),
    "transport.slow_consumer": CodeMeta(
        domain="transport",
        default_retryable=True,
        description="NATS slow-consumer detected; message dropped by the broker.",
    ),
    "transport.error": CodeMeta(
        domain="transport",
        default_retryable=True,
        description="Generic NATS / network transport failure not covered by a more specific code (e.g. connection reset, protocol error).",  # noqa: E501
    ),
    "transport.payload_too_large": CodeMeta(
        domain="transport",
        default_retryable=False,
        description="Request payload exceeded the NATS server's max_payload limit.",
    ),
    # --- pool ----------------------------------------------------------------
    "pool.circuit_open": CodeMeta(
        domain="pool",
        default_retryable=True,
        description="WorkerPoolClient circuit breaker is open; call short-circuited without dispatching to a worker.",  # noqa: E501
    ),
    "pool.no_live_workers": CodeMeta(
        domain="pool",
        default_retryable=True,
        description="WorkerPoolClient exhausted its registry without reaching a healthy worker.",  # noqa: E501
    ),
    # --- worker --------------------------------------------------------------
    "worker.crash": CodeMeta(
        domain="worker",
        default_retryable=True,
        description="Worker process raised an unhandled exception.",
    ),
    "worker.validation": CodeMeta(
        domain="worker",
        default_retryable=False,
        description="Request payload failed domain-level validation inside the worker.",
    ),
    "worker.internal": CodeMeta(
        domain="worker",
        default_retryable=True,
        description="Worker encountered an internal error not covered by a more specific code.",  # noqa: E501
    ),
    "worker.capacity": CodeMeta(
        domain="worker",
        default_retryable=True,
        description="Worker rejected the request because its capacity limit (queue or pool) is exhausted; caller should retry after back-off.",  # noqa: E501
    ),
    "worker.busy": CodeMeta(
        domain="worker",
        default_retryable=True,
        description="Worker rejected the request because its concurrency limit is reached.",  # noqa: E501
    ),
    # --- cli -----------------------------------------------------------------
    "cli.auth": CodeMeta(
        domain="cli",
        default_retryable=False,
        description="CLI pool authentication failed (invalid or expired credentials).",
    ),
    "cli.session_lost": CodeMeta(
        domain="cli",
        default_retryable=True,
        description="CLI session was lost and could not be resumed.",
    ),
    "cli.parse": CodeMeta(
        domain="cli",
        default_retryable=False,
        description="CLI command string could not be parsed.",
    ),
    # --- llm -----------------------------------------------------------------
    "llm.rate_limit": CodeMeta(
        domain="llm",
        default_retryable=True,
        description="LLM provider returned a rate-limit / quota-exceeded error.",
    ),
    "llm.context_too_long": CodeMeta(
        domain="llm",
        default_retryable=False,
        description="Input tokens exceed the model's context window.",
    ),
    "llm.model_unavailable": CodeMeta(
        domain="llm",
        default_retryable=True,
        description="Requested LLM model is temporarily or permanently unavailable.",
    ),
    "llm.no_responders": CodeMeta(
        domain="llm",
        default_retryable=True,
        description="No LLM worker is subscribed on the expected NATS subject.",
    ),
    # --- voice ---------------------------------------------------------------
    "voice.engine_unavailable": CodeMeta(
        domain="voice",
        default_retryable=True,
        description="Voice engine (TTS/STT) is not reachable or has not started.",
    ),
    "voice.audio_invalid": CodeMeta(
        domain="voice",
        default_retryable=False,
        description="Audio payload is malformed, too short, or in an unsupported format.",  # noqa: E501
    ),
    # --- image ---------------------------------------------------------------
    "image.engine_unavailable": CodeMeta(
        domain="image",
        default_retryable=True,
        description="Image generation engine is not reachable or has not started.",
    ),
    "image.prompt_rejected": CodeMeta(
        domain="image",
        default_retryable=False,
        description="Image prompt was rejected by the engine's content policy.",
    ),
}
