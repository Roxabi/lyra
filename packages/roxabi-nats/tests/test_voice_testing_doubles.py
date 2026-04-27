"""Three-guard tests for roxabi_nats.testing.voice. See spec #764.

Moved from roxabi_contracts/tests/test_voice_testing_doubles.py per ADR-059 V6.
"""

from __future__ import annotations

import asyncio
import base64
import os
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest
from nats.aio.client import Client as NATS
from nats.errors import NoServersError

import nats as _nats
from roxabi_contracts.voice import (
    SttRequest,
    SttResponse,
    TtsRequest,
    TtsResponse,
)
from roxabi_contracts.voice.fixtures import (
    sample_transcript_en,
    silence_wav_16khz,
)
from roxabi_contracts.voice.subjects import SUBJECTS
from roxabi_nats.testing.voice import FakeSttWorker, FakeTtsWorker

requires_nats_server = pytest.mark.skipif(
    shutil.which("nats-server") is None,
    reason="nats-server not found in PATH — install via 'make nats-install'",
)


@pytest.fixture(autouse=True)
def _clear_lyra_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LYRA_ENV", raising=False)


@pytest.mark.parametrize("cls", [FakeTtsWorker, FakeSttWorker])
def test_g2_prod_env_raises(cls, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LYRA_ENV", "production")
    with pytest.raises(RuntimeError, match=f"{cls.__name__} cannot run in production"):
        cls()


@pytest.mark.parametrize("cls", [FakeTtsWorker, FakeSttWorker])
def test_g2_prod_env_raises_even_when_g3_would_pass(
    cls, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LYRA_ENV", "production")
    with pytest.raises(RuntimeError):
        cls(nats_url="nats://127.0.0.1:4222")


@pytest.mark.parametrize("cls", [FakeTtsWorker, FakeSttWorker])
@pytest.mark.parametrize(
    "bad_url",
    [
        "nats://10.0.0.5:4222",
        "nats://0.0.0.0:4222",
        "nats://localhost.evil.com:4222",
        "nats://example.com:4222",
    ],
)
async def test_g3_non_loopback_raises(cls, bad_url: str) -> None:
    w = cls(nats_url=bad_url)
    with pytest.raises(ValueError, match="loopback"):
        await w.start()


async def _run_loopback_passes_guard(cls: type, url: str) -> None:
    # No nats-server running on these ports in this unit test — assert the
    # guard does NOT raise ValueError, but some other error (connection
    # refused / timeout) occurs downstream. We only care that Guard 3 passed.
    w = cls(nats_url=url)
    with pytest.raises((OSError, asyncio.TimeoutError, NoServersError)):
        await w.start()


@pytest.mark.parametrize("cls", [FakeTtsWorker, FakeSttWorker])
async def test_g3_accepts_ipv4_loopback(cls) -> None:
    await _run_loopback_passes_guard(cls, "nats://127.0.0.1:4222")


@pytest.mark.parametrize("cls", [FakeTtsWorker, FakeSttWorker])
async def test_g3_accepts_ipv6_loopback(cls) -> None:
    await _run_loopback_passes_guard(cls, "nats://[::1]:4222")


@pytest.mark.parametrize("cls", [FakeTtsWorker, FakeSttWorker])
async def test_g3_accepts_ipv6_loopback_full(cls) -> None:
    await _run_loopback_passes_guard(cls, "nats://[0:0:0:0:0:0:0:1]:4222")


@pytest.mark.parametrize("cls", [FakeTtsWorker, FakeSttWorker])
async def test_g3_non_loopback_raises_when_g2_unset(cls) -> None:
    """Guard 3 fires even with LYRA_ENV unset — proves G3 independent of G2."""
    w = cls(nats_url="nats://10.0.0.5:4222")
    with pytest.raises(ValueError, match="loopback"):
        await w.start()


# ---------------------------------------------------------------------------
# Roundtrip + ordering + idempotent/double-start tests
# ---------------------------------------------------------------------------

_ENVELOPE: dict[str, Any] = {
    "contract_version": "1",
    "trace_id": "test-trace",
    "issued_at": datetime(2026, 4, 18, tzinfo=timezone.utc),
}


@requires_nats_server
async def test_tts_roundtrip_default_fixture(nats_server_url: str) -> None:
    worker = FakeTtsWorker(nats_url=nats_server_url)
    await worker.start()
    assert worker.calls == []
    try:
        nc = await _nats.connect(nats_server_url)
        req = TtsRequest(**_ENVELOPE, request_id="r1", text="hello")
        msg = await nc.request(
            SUBJECTS.tts_request, req.model_dump_json().encode(), timeout=2.0
        )
        reply = TtsResponse.model_validate_json(msg.data)
        assert reply.ok is True
        assert reply.request_id == "r1"
        assert reply.mime_type == "audio/wav"
        assert reply.audio_b64 is not None
        assert base64.b64decode(reply.audio_b64) == silence_wav_16khz
        assert len(worker.calls) == 1
        assert worker.calls[0].text == "hello"
        await nc.close()
    finally:
        await worker.stop()
        await asyncio.sleep(0.05)


@requires_nats_server
async def test_stt_roundtrip_default_fixture(nats_server_url: str) -> None:
    worker = FakeSttWorker(nats_url=nats_server_url)
    await worker.start()
    assert worker.calls == []
    try:
        nc = await _nats.connect(nats_server_url)
        req = SttRequest(
            **_ENVELOPE,
            request_id="r2",
            audio_b64=base64.b64encode(silence_wav_16khz).decode("ascii"),
            model="large-v3-turbo",
        )
        msg = await nc.request(
            SUBJECTS.stt_request, req.model_dump_json().encode(), timeout=2.0
        )
        reply = SttResponse.model_validate_json(msg.data)
        assert reply.ok is True
        assert reply.request_id == "r2"
        assert reply.text == sample_transcript_en
        assert reply.language == "en"
        assert reply.duration_seconds == 1.0
        assert len(worker.calls) == 1
        await nc.close()
    finally:
        await worker.stop()
        await asyncio.sleep(0.05)


@requires_nats_server
async def test_calls_records_multiple_requests_in_order(
    nats_server_url: str,
) -> None:
    worker = FakeTtsWorker(nats_url=nats_server_url)
    await worker.start()
    assert worker.calls == []
    try:
        nc = await _nats.connect(nats_server_url)
        for i in range(3):
            req = TtsRequest(**_ENVELOPE, request_id=f"r{i}", text=f"msg-{i}")
            await nc.request(
                SUBJECTS.tts_request, req.model_dump_json().encode(), timeout=2.0
            )
        assert [r.request_id for r in worker.calls] == ["r0", "r1", "r2"]
        await nc.close()
    finally:
        await worker.stop()
        await asyncio.sleep(0.05)


@requires_nats_server
async def test_dispatch_drops_malformed_json(nats_server_url: str) -> None:
    """_dispatch silently drops requests that fail Pydantic validation."""
    worker = FakeTtsWorker(nats_url=nats_server_url)
    await worker.start()
    assert worker.calls == []
    try:
        nc = await _nats.connect(nats_server_url)
        await nc.publish(SUBJECTS.tts_request, b"this is not json at all")
        await asyncio.sleep(0.1)
        assert worker.calls == []
        await nc.close()
    finally:
        await worker.stop()
        await asyncio.sleep(0.05)


@requires_nats_server
async def test_dispatch_no_reply_records_call_without_publishing(
    nats_server_url: str,
) -> None:
    """Fire-and-forget publish (no reply subject) records the call, sends no reply."""
    worker = FakeTtsWorker(nats_url=nats_server_url)
    await worker.start()
    assert worker.calls == []
    try:
        nc = await _nats.connect(nats_server_url)
        req = TtsRequest(**_ENVELOPE, request_id="r-fire", text="hello")
        await nc.publish(SUBJECTS.tts_request, req.model_dump_json().encode())
        await asyncio.sleep(0.1)
        assert len(worker.calls) == 1
        assert worker.calls[0].request_id == "r-fire"
        await nc.close()
    finally:
        await worker.stop()
        await asyncio.sleep(0.05)


async def test_stop_is_idempotent() -> None:
    worker = FakeTtsWorker()
    await worker.stop()
    await worker.stop()


async def test_stop_nulls_disconnected_nc() -> None:
    """stop() must null _nc/_sub even when _nc.is_connected is False."""
    from unittest.mock import MagicMock

    for cls in (FakeTtsWorker, FakeSttWorker):
        worker = cls()
        mock_nc = MagicMock()
        mock_nc.is_connected = False
        worker._nc = mock_nc  # type: ignore[assignment]
        worker._sub = object()  # type: ignore[assignment]
        await worker.stop()
        assert worker._nc is None
        assert worker._sub is None
        mock_nc.drain.assert_not_called()


async def test_start_twice_raises() -> None:
    """start() with a live _nc must raise RuntimeError."""
    worker = FakeTtsWorker()
    worker._nc = cast(NATS, object())
    with pytest.raises(RuntimeError, match="already started"):
        await worker.start()


# ---------------------------------------------------------------------------
# Guard 1 subprocess test + API surface invariant
# ---------------------------------------------------------------------------


def test_voice_init_does_not_expose_testing() -> None:
    """Regression guard — roxabi_contracts.voice.__init__ must NOT re-export testing."""
    import roxabi_contracts.voice as voice_mod

    assert "testing" not in voice_mod.__all__
    import inspect

    src = inspect.getsource(voice_mod)
    assert "testing" not in src


@pytest.mark.parametrize("cls", [FakeTtsWorker, FakeSttWorker])
@pytest.mark.parametrize(
    "value", ["production", "PRODUCTION", "Production", "pRoDuCtIoN"]
)
def test_g2_prod_env_case_insensitive(
    cls, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LYRA_ENV", value)
    with pytest.raises(RuntimeError, match=f"{cls.__name__} cannot run in production"):
        cls()


def test_g1_import_without_extra(tmp_path: Path) -> None:
    """Guard 1 — `import nats` at the top of testing.voice fires when nats-py is absent.

    Stubs are injected for all roxabi_nats submodules that import nats at their own
    module top (adapter_base, connect, driver_base, _serialize), so the sabotaged
    nats.py is only hit by the Guard 1 tripwire line in roxabi_nats.testing.voice.
    """
    sabotage = tmp_path / "nats.py"
    sabotage.write_text(
        textwrap.dedent(
            """
            raise ModuleNotFoundError("No module named 'nats' (sabotaged)", name="nats")
            """
        ).lstrip()
    )
    script = textwrap.dedent(
        """
        import sys, types

        def _stub(name, **attrs):
            m = types.ModuleType(name)
            for k, v in attrs.items():
                setattr(m, k, v)
            sys.modules[name] = m

        # Stub submodules that import nats at their module top so only
        # the Guard 1 tripwire in testing/voice.py consumes the sabotage.
        _stub("roxabi_nats.adapter_base", NatsAdapterBase=None)
        _stub("roxabi_nats.connect", nats_connect=None)
        _stub("roxabi_nats.driver_base", NatsDriverBase=None)
        _stub("roxabi_nats._serialize", _TypeHintResolver=object)
        _stub(
            "roxabi_nats.testing._guards",
            assert_not_production=lambda cls_name: None,
            assert_loopback_url=lambda url: None,
        )

        try:
            import roxabi_nats.testing.voice  # noqa: F401
        except ModuleNotFoundError as exc:
            if exc.name == "nats":
                sys.exit(42)
            raise
        sys.exit(0)
        """
    ).lstrip()
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={
            **os.environ,
            "PYTHONPATH": f"{tmp_path}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 42, (
        f"expected exit 42 (ModuleNotFoundError for nats), got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
