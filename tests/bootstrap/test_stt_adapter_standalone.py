"""Tests for stt_adapter_standalone — RED-phase tests for NatsAdapterBase migration.

These tests verify the post-migration shape of stt_adapter_standalone.py:
  - No bare nats.connect() / import nats usage
  - STT_WORKERS constant is used (not a hardcoded QUEUE_GROUP string)
  - _bootstrap_stt_adapter_standalone retains its async signature
  - SttAdapterStandalone class exists and inherits from NatsAdapterBase
  - _mime_to_ext helper remains accessible at module level

Tests 1, 2, and 4 are RED before migration (they fail against current source).
Tests 3 and 5 pass both before and after migration (signature and helper unchanged).
"""
from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SOURCE = (
    Path(__file__).parent.parent.parent / "src/lyra/bootstrap/stt_adapter_standalone.py"
)


def test_stt_adapter_source_uses_no_bare_nats_connect() -> None:
    """Source must not contain bare nats.connect() or 'import nats' after migration.

    RED gate: current source has `nats.connect(` and `import nats\\n` — this fails
    until the migration to NatsAdapterBase removes them.
    """
    source = _SOURCE.read_text()
    assert "nats.connect(" not in source, (
        "stt_adapter_standalone still uses bare nats.connect()"
    )
    assert "import nats\n" not in source, (
        "stt_adapter_standalone still has bare 'import nats'"
    )


def test_stt_adapter_uses_stt_workers_constant() -> None:
    """Source must reference STT_WORKERS, not a hardcoded QUEUE_GROUP string.

    RED gate: current source uses a bare QUEUE_GROUP string; STT_WORKERS is not
    imported or defined until migration.
    """
    source = _SOURCE.read_text()
    assert "STT_WORKERS" in source, (
        "stt_adapter_standalone does not reference STT_WORKERS — "
        "migration must import and pass this constant to NatsAdapterBase"
    )


def test_bootstrap_stt_adapter_standalone_signature() -> None:
    """_bootstrap_stt_adapter_standalone must be async with the expected params.

    This test passes before and after migration — the public signature is unchanged.
    """
    from lyra.bootstrap.stt_adapter_standalone import _bootstrap_stt_adapter_standalone

    assert inspect.iscoroutinefunction(_bootstrap_stt_adapter_standalone), (
        "_bootstrap_stt_adapter_standalone must be a coroutine function"
    )

    sig = inspect.signature(_bootstrap_stt_adapter_standalone)
    params = list(sig.parameters)

    assert params[0] == "raw_config", (
        f"First parameter must be 'raw_config', got '{params[0]}'"
    )

    stop_param = sig.parameters.get("_stop")
    assert stop_param is not None, "Keyword-only parameter '_stop' must exist"
    assert stop_param.kind == inspect.Parameter.KEYWORD_ONLY, (
        "'_stop' must be a keyword-only parameter"
    )


def test_stt_adapter_standalone_class_exists() -> None:
    """SttAdapterStandalone must exist and subclass NatsAdapterBase after migration.

    RED gate: the class does not exist in current source — ImportError expected
    before migration.
    """
    from lyra.nats import NatsAdapterBase

    try:
        from lyra.bootstrap.stt_adapter_standalone import SttAdapterStandalone
    except ImportError as exc:
        pytest.fail(
            f"SttAdapterStandalone not found in stt_adapter_standalone — "
            f"migration has not been applied yet: {exc}"
        )

    assert issubclass(SttAdapterStandalone, NatsAdapterBase), (
        "SttAdapterStandalone must subclass NatsAdapterBase"
    )


def test_mime_to_ext_still_accessible() -> None:
    """_mime_to_ext must remain a module-level helper after migration.

    This test passes before and after migration — the helper is kept at module level.
    """
    from lyra.bootstrap.stt_adapter_standalone import _mime_to_ext

    assert _mime_to_ext("audio/ogg") == ".ogg", (
        "_mime_to_ext('audio/ogg') must return '.ogg'"
    )
    assert _mime_to_ext("audio/mpeg") == ".mp3", (
        "_mime_to_ext('audio/mpeg') must return '.mp3'"
    )
    assert _mime_to_ext("audio/wav") == ".wav", (
        "_mime_to_ext('audio/wav') must return '.wav'"
    )
    assert _mime_to_ext("audio/unknown") == ".ogg", (
        "_mime_to_ext with unknown MIME must fall back to '.ogg'"
    )


class TestSttHeartbeatPayload:
    """Behavioural tests for SttAdapterStandalone heartbeat opt-in (T9/T11)."""

    def _make_adapter(self):
        """Build SttAdapterStandalone with mocked STTService."""
        from lyra.bootstrap.stt_adapter_standalone import SttAdapterStandalone
        from lyra.stt import STTConfig

        mock_cfg = STTConfig(model_size="large-v3-turbo")
        mock_stt_service = MagicMock()

        with (
            patch(
                "lyra.bootstrap.stt_adapter_standalone.load_stt_config",
                return_value=mock_cfg,
            ),
            patch(
                "lyra.bootstrap.stt_adapter_standalone.STTService",
                return_value=mock_stt_service,
            ),
        ):
            adapter = SttAdapterStandalone(raw_config={})

        return adapter

    def test_heartbeat_subject_is_stt_subject(self) -> None:
        """SttAdapterStandalone passes heartbeat_subject='lyra.voice.stt.heartbeat'."""
        adapter = self._make_adapter()
        assert adapter._heartbeat_subject == "lyra.voice.stt.heartbeat", (
            f"Expected heartbeat_subject='lyra.voice.stt.heartbeat', "
            f"got {adapter._heartbeat_subject!r}"
        )

    def test_heartbeat_payload_includes_model_loaded(self) -> None:
        """heartbeat_payload() includes 'model_loaded' key matching the model size."""
        adapter = self._make_adapter()
        payload = adapter.heartbeat_payload()
        assert "model_loaded" in payload, (
            "heartbeat_payload() must include 'model_loaded'"
        )
        assert payload["model_loaded"] == "large-v3-turbo", (
            f"Expected model_loaded='large-v3-turbo', got {payload['model_loaded']!r}"
        )

    def test_heartbeat_payload_includes_vram_fields(self) -> None:
        """heartbeat_payload() includes 'vram_used_mb' and 'vram_total_mb' as int."""
        adapter = self._make_adapter()
        payload = adapter.heartbeat_payload()
        assert "vram_used_mb" in payload, (
            "heartbeat_payload() must include 'vram_used_mb'"
        )
        assert "vram_total_mb" in payload, (
            "heartbeat_payload() must include 'vram_total_mb'"
        )
        assert isinstance(payload["vram_used_mb"], int), (
            f"vram_used_mb must be int, got {type(payload['vram_used_mb'])}"
        )
        assert isinstance(payload["vram_total_mb"], int), (
            f"vram_total_mb must be int, got {type(payload['vram_total_mb'])}"
        )

    def test_heartbeat_payload_includes_active_requests(self) -> None:
        """heartbeat_payload() includes 'active_requests' as int."""
        adapter = self._make_adapter()
        payload = adapter.heartbeat_payload()
        assert "active_requests" in payload, (
            "heartbeat_payload() must include 'active_requests'"
        )
        assert isinstance(payload["active_requests"], int), (
            f"active_requests must be int, got {type(payload['active_requests'])}"
        )
        assert payload["active_requests"] == 0, (
            f"active_requests must start at 0, got {payload['active_requests']}"
        )

    def test_heartbeat_payload_includes_base_fields(self) -> None:
        """heartbeat_payload() includes base fields: worker_id, service, ts, etc."""
        adapter = self._make_adapter()
        payload = adapter.heartbeat_payload()
        for field in ("worker_id", "service", "ts", "host", "subject", "queue_group"):
            assert field in payload, (
                f"heartbeat_payload() must include base field '{field}'"
            )
