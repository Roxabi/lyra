"""Hub-side LLM user-error resolution (ADR-089).

Maps ``WorkerError`` (from any ``LlmProvider`` backend — clipool, omp-rpc,
in-process CliPool) to a user-safe display string via ``MessageManager`` templates
or a curated passthrough allowlist.

Workers classify; codecs transport; this module presents. See
``docs/architecture/adr/089-centralized-llm-user-error-resolution.mdx``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from factory.core.messaging.message import GENERIC_ERROR_REPLY
from roxabi_contracts.errors import KNOWN_CODES, WorkerError

if TYPE_CHECKING:
    from factory.core.messaging.messages import MessageManager

__all__ = ["resolve_user_error"]

# Template keys in messages.toml [errors.{lang}].
_CODE_TO_TEMPLATE: dict[str, str] = {
    "transport.timeout": "timeout",
    "pool.circuit_open": "unavailable",
    "cli.auth": "auth_required",
    "llm.rate_limit": "rate_limit",
    "llm.context_too_long": "context_too_long",
    "llm.model_unavailable": "unavailable",
}

# Codes whose WorkerError.message was scrubbed upstream and may be shown verbatim.
# Review when adding new KNOWN_CODES entries (ADR-089 § Security invariants).
_PASSTHROUGH_CODES: frozenset[str] = frozenset(
    {
        "cli.parse",
        "cli.session_lost",
        "llm.rate_limit",
    }
)

# Infra / internal codes — never forward message (may be type(exc).__name__ only).
_GENERIC_ONLY_CODES: frozenset[str] = frozenset(
    {
        "worker.crash",
        "worker.internal",
        "worker.validation",
        "worker.capacity",
        "worker.busy",
        "stream.error",
        "transport.error",
        "transport.parse",
        "transport.no_responders",
        "transport.contract_mismatch",
        "transport.slow_consumer",
        "transport.payload_too_large",
    }
)


def _generic(msg_manager: MessageManager | None) -> str:
    if msg_manager is not None:
        return msg_manager.get("generic") or GENERIC_ERROR_REPLY
    return GENERIC_ERROR_REPLY


def _from_template(
    key: str,
    msg_manager: MessageManager | None,
    *,
    fallback: str,
    **kwargs: str,
) -> str:
    if msg_manager is not None:
        text = msg_manager.get(key, **kwargs)
        if text:
            return text
    return fallback


def resolve_user_error(
    *,
    worker_error: WorkerError | None = None,
    error_text: str | None = None,
    msg_manager: MessageManager | None = None,
    bot_name: str | None = None,
) -> str:
    """Map a structured LLM error to a user-safe display string.

    Parameters
    ----------
    worker_error:
        Structured envelope from worker/codec (ADR-066). Preferred source.
    error_text:
        Legacy flat error string (``LlmResult.error`` or ``ResultLlmEvent.error_text``).
        Used only when ``worker_error`` is absent.
    msg_manager:
        Hub-injected ``MessageManager`` for i18n; ``None`` uses English fallbacks.

    Returns
    -------
    str
        Text safe to place in ``Response.content`` or ``RunErrorRenderEvent.message``.
    """
    if worker_error is not None:
        code = worker_error.code
        if code in _GENERIC_ONLY_CODES or (
            code in KNOWN_CODES and code not in _CODE_TO_TEMPLATE and code not in _PASSTHROUGH_CODES
        ):
            return _generic(msg_manager)

        template_key = _CODE_TO_TEMPLATE.get(code)
        if template_key is not None:
            kwargs: dict[str, str] = {}
            if bot_name is not None:
                kwargs["bot_name"] = bot_name
            return _from_template(
                template_key,
                msg_manager,
                fallback=worker_error.message or _generic(msg_manager),
                **kwargs,
            )

        if code in _PASSTHROUGH_CODES and worker_error.message:
            return worker_error.message

        return _generic(msg_manager)

    if error_text:
        if "Timeout" in error_text or "timed out" in error_text.lower():
            return _from_template(
                "timeout",
                msg_manager,
                fallback="Your request timed out. Please try again.",
            )

    return _generic(msg_manager)