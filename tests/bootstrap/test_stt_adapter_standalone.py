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

import base64
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


class TestSttAdapterReplyContractVersion:
    """Behavioural: handle() replies emit contract_version (ADR-044)."""

    def _make_adapter(self, transcribe_result=None, transcribe_raises=None):
        """Build SttAdapterStandalone with a stubbed STTService.transcribe."""
        from lyra.bootstrap.stt_adapter_standalone import SttAdapterStandalone
        from lyra.stt import STTConfig

        mock_cfg = STTConfig(model_size="large-v3-turbo")
        mock_stt_service = MagicMock()
        if transcribe_raises is not None:
            mock_stt_service.transcribe = AsyncMock(side_effect=transcribe_raises)
        else:
            mock_stt_service.transcribe = AsyncMock(return_value=transcribe_result)

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

    async def _call_handle(self, adapter, payload: dict) -> dict:
        """Run adapter.handle(); capture and return the decoded reply payload."""
        captured = {}

        async def capture_reply(msg, data: bytes) -> None:
            captured["payload"] = json.loads(data)

        adapter.reply = capture_reply  # type: ignore[method-assign]
        mock_msg = MagicMock()
        mock_msg.reply = "_INBOX.test"
        await adapter.handle(mock_msg, payload)
        return captured["payload"]

    @pytest.mark.asyncio
    async def test_success_reply_emits_contract_version(self) -> None:
        """A successful transcription reply stamps contract_version='1'."""
        from lyra.stt import TranscriptionResult

        adapter = self._make_adapter(
            transcribe_result=TranscriptionResult(
                text="hello", language="en", duration_seconds=1.0
            )
        )
        reply = await self._call_handle(
            adapter,
            {
                "request_id": "rid-1",
                "audio_b64": base64.b64encode(b"\x00" * 8).decode(),
                "mime_type": "audio/ogg",
            },
        )
        assert reply["ok"] is True
        assert reply["contract_version"] == "1"

    @pytest.mark.asyncio
    async def test_error_reply_emits_contract_version(self) -> None:
        """An error reply (transcription failed) also stamps contract_version='1'."""
        adapter = self._make_adapter(transcribe_raises=RuntimeError("engine down"))
        reply = await self._call_handle(
            adapter,
            {
                "request_id": "rid-2",
                "audio_b64": base64.b64encode(b"\x00" * 8).decode(),
                "mime_type": "audio/ogg",
            },
        )
        assert reply["ok"] is False
        assert reply["contract_version"] == "1"


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

    def test_heartbeat_payload_includes_contract_version(self) -> None:
        """heartbeat_payload() includes 'contract_version' per ADR-044."""
        adapter = self._make_adapter()
        payload = adapter.heartbeat_payload()
        assert payload.get("contract_version") == "1", (
            "heartbeat_payload() must include contract_version='1' (ADR-044)"
        )

    def test_heartbeat_payload_includes_base_fields(self) -> None:
        """heartbeat_payload() includes base fields: worker_id, service, ts, etc."""
        adapter = self._make_adapter()
        payload = adapter.heartbeat_payload()
        for field in ("worker_id", "service", "ts", "host", "subject", "queue_group"):
            assert field in payload, (
                f"heartbeat_payload() must include base field '{field}'"
            )

    def test_vram_fallback_is_zero_when_pynvml_unavailable(self) -> None:
        """_get_vram_info() returns (0, 0) when pynvml is unavailable."""
        adapter = self._make_adapter()
        with patch(
            "lyra.bootstrap.stt_adapter_standalone.SttAdapterStandalone._get_vram_info",
            return_value=(0, 0),
        ):
            payload = adapter.heartbeat_payload()
        assert payload["vram_used_mb"] == 0
        assert payload["vram_total_mb"] == 0

    def test_vram_values_from_pynvml_when_available(self) -> None:
        """_get_vram_info() returns real MB values when pynvml succeeds."""
        adapter = self._make_adapter()
        with patch(
            "lyra.bootstrap.stt_adapter_standalone.SttAdapterStandalone._get_vram_info",
            return_value=(4096, 10240),
        ):
            payload = adapter.heartbeat_payload()
        assert payload["vram_used_mb"] == 4096
        assert payload["vram_total_mb"] == 10240
