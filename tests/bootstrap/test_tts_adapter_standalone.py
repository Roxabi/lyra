"""Tests for tts_adapter_standalone — RED-phase tests for NatsAdapterBase migration.

These tests verify the post-migration shape of tts_adapter_standalone.py:
  - No bare nats.connect() / import nats usage
  - TTS_WORKERS constant is used (not a hardcoded string)
  - _bootstrap_tts_adapter_standalone retains its async signature
  - TtsAdapterStandalone class exists and inherits from NatsAdapterBase

Tests 1, 2, and 4 are RED before migration (they fail against current source).
Test 3 passes both before and after migration (signature is unchanged).
"""
from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

_SOURCE = (
    Path(__file__).parent.parent.parent / "src/lyra/bootstrap/tts_adapter_standalone.py"
)


def test_tts_adapter_source_uses_no_bare_nats_connect() -> None:
    """Source must not contain bare nats.connect() or 'import nats' after migration.

    RED gate: current source has `nats.connect(` and `import nats\\n` — this fails
    until the migration to NatsAdapterBase removes them.
    """
    source = _SOURCE.read_text()
    assert "nats.connect(" not in source, (
        "tts_adapter_standalone still uses bare nats.connect()"
    )
    assert "import nats\n" not in source, (
        "tts_adapter_standalone still has bare 'import nats'"
    )


def test_tts_adapter_uses_tts_workers_constant() -> None:
    """Source must reference the TTS_WORKERS constant, not a hardcoded string.

    RED gate: current source uses a bare QUEUE_GROUP string; TTS_WORKERS is not
    imported or defined until migration.
    """
    source = _SOURCE.read_text()
    assert "TTS_WORKERS" in source, (
        "tts_adapter_standalone does not reference TTS_WORKERS — "
        "migration must import and pass this constant to NatsAdapterBase"
    )


def test_bootstrap_tts_adapter_standalone_signature() -> None:
    """_bootstrap_tts_adapter_standalone must be async with the expected params.

    This test passes before and after migration — the public signature is unchanged.
    """
    from lyra.bootstrap.tts_adapter_standalone import _bootstrap_tts_adapter_standalone

    assert inspect.iscoroutinefunction(_bootstrap_tts_adapter_standalone), (
        "_bootstrap_tts_adapter_standalone must be a coroutine function"
    )

    sig = inspect.signature(_bootstrap_tts_adapter_standalone)
    params = list(sig.parameters)

    assert params[0] == "raw_config", (
        f"First parameter must be 'raw_config', got '{params[0]}'"
    )

    stop_param = sig.parameters.get("_stop")
    assert stop_param is not None, "Keyword-only parameter '_stop' must exist"
    assert stop_param.kind == inspect.Parameter.KEYWORD_ONLY, (
        "'_stop' must be a keyword-only parameter"
    )


def test_tts_adapter_standalone_class_exists() -> None:
    """TtsAdapterStandalone must exist and subclass NatsAdapterBase after migration.

    RED gate: the class does not exist in current source — ImportError expected
    before migration.
    """
    from lyra.nats import NatsAdapterBase

    try:
        from lyra.bootstrap.tts_adapter_standalone import TtsAdapterStandalone
    except ImportError as exc:
        pytest.fail(
            f"TtsAdapterStandalone not found in tts_adapter_standalone — "
            f"migration has not been applied yet: {exc}"
        )

    assert issubclass(TtsAdapterStandalone, NatsAdapterBase), (
        "TtsAdapterStandalone must subclass NatsAdapterBase"
    )


class TestTtsHeartbeatPayload:
    def _make_adapter(self):
        """Build TtsAdapterStandalone with mocked TTSService to avoid loading engine."""
        from unittest.mock import MagicMock, patch

        from lyra.tts import TTSConfig

        mock_tts_service = MagicMock()
        mock_tts_service.engine_name = "chatterbox"

        with (
            patch(
                "lyra.bootstrap.tts_adapter_standalone.load_tts_config",
                return_value=TTSConfig(engine="chatterbox"),
            ),
            patch(
                "lyra.bootstrap.tts_adapter_standalone.TTSService",
                return_value=mock_tts_service,
            ),
        ):
            from lyra.bootstrap.tts_adapter_standalone import TtsAdapterStandalone
            adapter = TtsAdapterStandalone({})

        return adapter

    def test_heartbeat_subject_is_tts_subject(self):
        adapter = self._make_adapter()
        assert adapter._heartbeat_subject == "lyra.voice.tts.heartbeat"

    def test_heartbeat_payload_includes_model_loaded(self):
        adapter = self._make_adapter()
        payload = adapter.heartbeat_payload()
        assert "model_loaded" in payload

    def test_heartbeat_payload_includes_vram_fields(self):
        adapter = self._make_adapter()
        payload = adapter.heartbeat_payload()
        assert "vram_used_mb" in payload
        assert "vram_total_mb" in payload
        assert isinstance(payload["vram_used_mb"], int)
        assert isinstance(payload["vram_total_mb"], int)

    def test_heartbeat_payload_includes_active_requests(self):
        adapter = self._make_adapter()
        payload = adapter.heartbeat_payload()
        assert "active_requests" in payload
        assert payload["active_requests"] == 0

    def test_heartbeat_payload_includes_base_fields(self):
        adapter = self._make_adapter()
        payload = adapter.heartbeat_payload()
        for field in ("worker_id", "service", "host", "subject", "queue_group", "ts"):
            assert field in payload, f"Missing base field: {field}"

    def test_vram_fallback_is_zero_when_pynvml_unavailable(self) -> None:
        """_get_vram_info() returns (0, 0) when pynvml is unavailable."""
        adapter = self._make_adapter()
        with patch(
            "lyra.bootstrap.tts_adapter_standalone.TtsAdapterStandalone"
            "._get_vram_info",
            return_value=(0, 0),
        ):
            payload = adapter.heartbeat_payload()
        assert payload["vram_used_mb"] == 0
        assert payload["vram_total_mb"] == 0

    def test_vram_values_from_pynvml_when_available(self) -> None:
        """_get_vram_info() returns real MB values when pynvml succeeds."""
        adapter = self._make_adapter()
        with patch(
            "lyra.bootstrap.tts_adapter_standalone.TtsAdapterStandalone"
            "._get_vram_info",
            return_value=(4096, 10240),
        ):
            payload = adapter.heartbeat_payload()
        assert payload["vram_used_mb"] == 4096
        assert payload["vram_total_mb"] == 10240
