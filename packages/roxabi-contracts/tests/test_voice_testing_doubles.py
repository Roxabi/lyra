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


@pytest.mark.parametrize("cls", [FakeTtsWorker, FakeSttWorker])
@pytest.mark.parametrize(
    "ok_url",
    ["nats://127.0.0.1:4222", "nats://[::1]:4222", "nats://[0:0:0:0:0:0:0:1]:4222"],
)
async def test_g3_accepts_loopback_but_refuses_connect_without_server(
    cls, ok_url: str
) -> None:
    # No nats-server running on these ports in this unit test — assert the
    # guard does NOT raise ValueError, but some other error (connection
    # refused / timeout) occurs downstream. We only care that Guard 3 passed.
    w = cls(nats_url=ok_url)
    with pytest.raises(Exception) as exc_info:
        await w.start()
    assert not isinstance(exc_info.value, ValueError) or "loopback" not in str(
        exc_info.value
    )


# ---------------------------------------------------------------------------
# Slice V2: roundtrip + ordering + idempotent/double-start tests
# ---------------------------------------------------------------------------

import base64  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import textwrap  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import nats as _nats  # noqa: E402
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

from .conftest import requires_nats_server  # noqa: E402

_ENVELOPE: dict[str, Any] = {
    "contract_version": "1",
    "trace_id": "test-trace",
    "issued_at": datetime(2026, 4, 18, tzinfo=timezone.utc),
}


@requires_nats_server
async def test_tts_roundtrip_default_fixture(nats_server_url: str) -> None:
    worker = FakeTtsWorker(nats_url=nats_server_url)
    await worker.start()
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


@requires_nats_server
async def test_stt_roundtrip_default_fixture(nats_server_url: str) -> None:
    worker = FakeSttWorker(nats_url=nats_server_url)
    await worker.start()
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


@requires_nats_server
async def test_calls_records_multiple_requests_in_order(
    nats_server_url: str,
) -> None:
    worker = FakeTtsWorker(nats_url=nats_server_url)
    await worker.start()
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
# Slice V3: Guard 1 subprocess test
# ---------------------------------------------------------------------------


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
        env={"PYTHONPATH": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 42, (
        f"expected exit 42 (ModuleNotFoundError for nats), got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
