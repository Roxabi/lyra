"""WorkerError envelope + KNOWN_CODES registry.

See docs/architecture/adr/066-unified-worker-error-envelope-nats-reply-contracts.mdx.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

__all__ = ["WorkerError", "CodeMeta", "KNOWN_CODES"]


# Scrub NATS connection-string credentials from message/detail strings.
# Pattern matches `scheme://user:pass@host` for nats / nats+tls / amqp / redis / http(s)
# and replaces the userinfo with `***:***`. Defence-in-depth against accidentally
# embedding `str(exc)` from a transport error that includes the connect URL.
_CREDENTIAL_RE = re.compile(
    r"((?:nats|nats\+tls|amqp|amqps|redis|rediss|https?|postgres(?:ql)?|mysql)://)"
    r"[^/@\s:]+:[^/@\s]+@"
)


def _scrub(value: str) -> str:
    return _CREDENTIAL_RE.sub(r"\1***:***@", value)


class WorkerError(BaseModel):
    """Structured error returned on NATS reply subjects by all Lyra workers."""

    code: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9._-]*$")
    message: str = Field(min_length=1, max_length=512)
    retryable: bool = True
    detail: str | None = Field(default=None, max_length=2048)

    model_config = {"extra": "ignore"}

    @field_validator("message", "detail")
    @classmethod
    def _strip_credentials(cls, v: str | None) -> str | None:
        return _scrub(v) if isinstance(v, str) else v


class CodeMeta(BaseModel):
    """Metadata for a registered error code."""

    domain: str
    default_retryable: bool
    description: str


# ---------------------------------------------------------------------------
# KNOWN_CODES — canonical registry (ADR-066 § "The code namespace")
# ---------------------------------------------------------------------------
# Domains: transport | worker | cli | llm | voice | image
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
