"""Three-guard tests for roxabi_contracts.voice.testing. See spec #764."""
from __future__ import annotations

import pytest

# Imports are here (not inside fixtures) to prove the module loads in the
# test env — Guard 1 (import-time) is exercised in a separate subprocess
# test in Slice V3.
from roxabi_contracts.voice.testing import FakeSttWorker, FakeTtsWorker


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
    # If Guard 3 fired incorrectly, its ValueError would not match the tuple
    # below and pytest.raises would fail, catching the regression.
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
    """Guard 3 fires even with LYRA_ENV unset — proves G3 independent of G2.

    The `_clear_lyra_env` autouse fixture ensures LYRA_ENV is unset for
    this test. This mirrors the spec's guard independence matrix row
    explicitly rather than relying on the autouse fixture implicitly.
    """
    w = cls(nats_url="nats://10.0.0.5:4222")
    with pytest.raises(ValueError, match="loopback"):
        await w.start()


# ---------------------------------------------------------------------------
# Slice V2: roundtrip + ordering + idempotent/double-start tests
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402
import base64  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import textwrap  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

from nats.errors import NoServersError  # noqa: E402

import nats as _nats  # noqa: E402
from _markers import requires_nats_server  # noqa: E402
from roxabi_contracts.voice import (  # noqa: E402
    SttRequest,
    SttResponse,
    TtsRequest,
    TtsResponse,
)
from roxabi_contracts.voice.fixtures import (  # noqa: E402
    sample_transcript_en,
    silence_wav_16khz,
)
from roxabi_contracts.voice.subjects import SUBJECTS  # noqa: E402

_ENVELOPE: dict[str, Any] = {
    "contract_version": "1",
    "trace_id": "test-trace",
    "issued_at": datetime(2026, 4, 18, tzinfo=timezone.utc),
}


@requires_nats_server
async def test_tts_roundtrip_default_fixture(nats_server_url: str) -> None:
    worker = FakeTtsWorker(nats_url=nats_server_url)
    await worker.start()
    assert worker.calls == []  # contamination check — prior test leaked?
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
        await asyncio.sleep(0.05)  # session-scoped fixture drain barrier


@requires_nats_server
async def test_stt_roundtrip_default_fixture(nats_server_url: str) -> None:
    worker = FakeSttWorker(nats_url=nats_server_url)
    await worker.start()
    assert worker.calls == []  # contamination check — prior test leaked?
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
        await asyncio.sleep(0.05)  # session-scoped fixture drain barrier


@requires_nats_server
async def test_calls_records_multiple_requests_in_order(
    nats_server_url: str,
) -> None:
    worker = FakeTtsWorker(nats_url=nats_server_url)
    await worker.start()
    assert worker.calls == []  # contamination check — prior test leaked?
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
        await asyncio.sleep(0.05)  # session-scoped fixture drain barrier


@requires_nats_server
async def test_dispatch_drops_malformed_json(nats_server_url: str) -> None:
    """_dispatch silently drops requests that fail Pydantic validation.

    Spec F8: `except ValidationError` path — log WARNING, no reply, no
    entry in `.calls`. Proves the drop-on-drift contract.
    """
    worker = FakeTtsWorker(nats_url=nats_server_url)
    await worker.start()
    assert worker.calls == []  # contamination check — prior test leaked?
    try:
        nc = await _nats.connect(nats_server_url)
        # Use publish (not request) because request would time out when
        # no reply arrives — publish + sleep to let the dispatch run.
        await nc.publish(SUBJECTS.tts_request, b"this is not json at all")
        await asyncio.sleep(0.1)  # let dispatch run
        assert worker.calls == []
        await nc.close()
    finally:
        await worker.stop()
        await asyncio.sleep(0.05)  # session-scoped fixture drain barrier


@requires_nats_server
async def test_dispatch_no_reply_records_call_without_publishing(
    nats_server_url: str,
) -> None:
    """Fire-and-forget publish (no reply subject) records the call but
    sends no reply. Spec F8: `if not msg.reply ... return` short-circuit.
    """
    worker = FakeTtsWorker(nats_url=nats_server_url)
    await worker.start()
    assert worker.calls == []  # contamination check — prior test leaked?
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
        await asyncio.sleep(0.05)  # session-scoped fixture drain barrier


async def test_stop_is_idempotent() -> None:
    worker = FakeTtsWorker()
    await worker.stop()
    await worker.stop()  # second call: no exception


async def test_start_twice_raises() -> None:
    """start() with a live _nc must raise RuntimeError."""
    # Bypass actual connection by setting _nc manually to exercise the check.
    worker = FakeTtsWorker()
    worker._nc = object()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="already started"):
        await worker.start()


# ---------------------------------------------------------------------------
# Slice V3: Guard 1 subprocess test + API surface invariant
# ---------------------------------------------------------------------------


def test_voice_init_does_not_expose_testing() -> None:
    """Regression guard — voice/__init__.py must NOT re-export testing.

    The testing module lives behind the [testing] extra; accidental re-export
    at the voice package root would mean a bare `import roxabi_contracts.voice`
    in a production install would trigger Guard 1's ModuleNotFoundError even
    when no caller wants test doubles. Spec #764 §API surface invariant.

    Note: after *any* submodule import Python injects the submodule into the
    parent package namespace, so `vars(voice_mod)` will contain 'testing'
    once this test suite has imported it. The meaningful invariant is that
    __init__.py does NOT import or list testing — checked via __all__ and
    source inspection.
    """
    import roxabi_contracts.voice as voice_mod

    # __all__ must not advertise the testing module
    assert "testing" not in voice_mod.__all__
    # __init__.py source must not contain an explicit import of testing
    import inspect

    src = inspect.getsource(voice_mod)
    assert "testing" not in src


@pytest.mark.parametrize("cls", [FakeTtsWorker, FakeSttWorker])
@pytest.mark.parametrize("value", ["PRODUCTION", "Production", "pRoDuCtIoN"])
def test_g2_prod_env_case_insensitive(
    cls, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LYRA_ENV", value)
    with pytest.raises(RuntimeError, match=f"{cls.__name__} cannot run in production"):
        cls()


def test_g1_import_without_extra(tmp_path: Path) -> None:
    """Guard 1 — importing roxabi_contracts.voice.testing without nats-py
    installed fails at import (not instantiation).

    Implementation: craft a temp dir containing a sabotaging `nats.py`
    that raises ModuleNotFoundError on execution, prepend it to
    PYTHONPATH so the subprocess's `import nats` hits it before the real
    package, then assert the import of `roxabi_contracts.voice.testing`
    fails. This proves Guard 1 fires at module-top-level (line: `import nats`).

    Regression guard: if a future change wraps `import nats` in a
    try/except inside testing.py, this test will fail (subprocess exits 0
    instead of 42), catching the regression before it reaches production.
    """
    sabotage = tmp_path / "nats.py"
    sabotage.write_text(
        textwrap.dedent(
            """
            raise ModuleNotFoundError("No module named 'nats' (sabotaged)")
            """
        ).lstrip()
    )
    script = textwrap.dedent(
        """
        import sys
        try:
            import roxabi_contracts.voice.testing  # noqa: F401
        except ModuleNotFoundError as exc:
            if "nats" in str(exc):
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
