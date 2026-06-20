"""Unit tests for resolve_user_error (ADR-089)."""

from __future__ import annotations

from factory.core.messaging.message import GENERIC_ERROR_REPLY
from factory.core.messaging.messages import MessageManager
from factory.core.messaging.utils.user_error_resolver import resolve_user_error
from roxabi_contracts.errors import WorkerError

_MESSAGES = (
    __import__("pathlib").Path(__file__).resolve().parents[2]
    / "src"
    / "factory"
    / "data"
    / "messages.toml"
)


class TestResolveUserErrorWorkerError:
    def test_generic_only_code_returns_generic(self) -> None:
        we = WorkerError(code="worker.crash", message="TimeoutError", retryable=True)
        assert resolve_user_error(worker_error=we) == GENERIC_ERROR_REPLY

    def test_transport_timeout_uses_template(self) -> None:
        mm = MessageManager(_MESSAGES, language="en")
        we = WorkerError(code="transport.timeout", message="timeout", retryable=True)
        result = resolve_user_error(worker_error=we, msg_manager=mm)
        assert result == mm.get("timeout")

    def test_cli_auth_uses_template(self) -> None:
        mm = MessageManager(_MESSAGES, language="en")
        we = WorkerError(
            code="cli.auth",
            message="Not logged in",
            retryable=False,
        )
        result = resolve_user_error(worker_error=we, msg_manager=mm)
        assert result == mm.get("auth_required")

    def test_cli_parse_passthrough_scrubbed_message(self) -> None:
        we = WorkerError(
            code="cli.parse",
            message="You've hit your weekly limit · resets 6pm (UTC)",
            retryable=False,
        )
        result = resolve_user_error(worker_error=we)
        assert "weekly limit" in result

    def test_llm_rate_limit_uses_template(self) -> None:
        mm = MessageManager(_MESSAGES, language="en")
        we = WorkerError(
            code="llm.rate_limit",
            message="Rate limit exceeded",
            retryable=True,
        )
        result = resolve_user_error(worker_error=we, msg_manager=mm)
        assert result == mm.get("rate_limit")

    def test_unknown_registered_code_falls_back_to_generic(self) -> None:
        we = WorkerError(
            code="voice.engine_unavailable",
            message="engine down",
            retryable=True,
        )
        assert resolve_user_error(worker_error=we) == GENERIC_ERROR_REPLY

    def test_pool_circuit_open_substitutes_bot_name_and_retry_secs(self) -> None:
        mm = MessageManager(_MESSAGES, language="en")
        we = WorkerError(
            code="pool.circuit_open",
            message="CircuitOpen",
            retryable=True,
            detail="45",
        )
        result = resolve_user_error(worker_error=we, msg_manager=mm, bot_name="Lyra")
        assert result == mm.get("unavailable", bot_name="Lyra", retry_secs="45")

    def test_llm_model_unavailable_substitutes_bot_name(self) -> None:
        mm = MessageManager(_MESSAGES, language="en")
        we = WorkerError(
            code="llm.model_unavailable",
            message="model down",
            retryable=True,
        )
        result = resolve_user_error(worker_error=we, msg_manager=mm, bot_name="Lyra")
        assert "Lyra" in result
        assert "{bot_name}" not in result

    def test_worker_error_preferred_over_error_text(self) -> None:
        we = WorkerError(code="worker.crash", message="internal", retryable=False)
        result = resolve_user_error(
            worker_error=we,
            error_text="should not surface",
        )
        assert result == GENERIC_ERROR_REPLY


class TestResolveUserErrorLegacyFlat:
    def test_timeout_string_uses_template(self) -> None:
        mm = MessageManager(_MESSAGES, language="en")
        result = resolve_user_error(
            error_text="Timeout: no output for 120s",
            msg_manager=mm,
        )
        assert result == mm.get("timeout")

    def test_flat_error_without_worker_error_returns_generic(self) -> None:
        result = resolve_user_error(error_text="Process died before send")
        assert result == GENERIC_ERROR_REPLY

    def test_empty_returns_generic(self) -> None:
        assert resolve_user_error() == GENERIC_ERROR_REPLY
