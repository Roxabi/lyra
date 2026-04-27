"""lyra voice-smoke — TTS→STT round-trip smoke test over NATS.

Exit 0 = PASS, 1 = FAIL. No voicecli import — NATS-only.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from uuid import uuid4

import nats.errors
import typer
from nats.aio.client import Client as NATS

from roxabi_nats.connect import nats_connect  # noqa: F401 — module-level for patching

_SMOKE_TEXT = "Voice cutover smoke test one two three"
_SMOKE_KEYWORDS = {"voice", "cutover", "smoke", "one", "two", "three"}

_TTS_SUBJECT = "lyra.voice.tts.request"
_STT_SUBJECT = "lyra.voice.stt.request"
_TTS_HEARTBEAT = "lyra.voice.tts.heartbeat"
_STT_HEARTBEAT = "lyra.voice.stt.heartbeat"
_VOICECLI_WORKER_PREFIX = "voicecli-"
_CONTRACT_VERSION = "1"

_DEFAULT_NATS_URL = "nats://localhost:4222"
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_HEARTBEAT_WAIT = 15.0

voice_smoke_app = typer.Typer(
    name="voice-smoke",
    help="TTS→STT round-trip smoke test over NATS.",
    invoke_without_command=True,
)


@voice_smoke_app.callback(invoke_without_command=True)
def voice_smoke(
    nats_url: str = typer.Option(  # noqa: B008
        None,
        "--nats-url",
        help=(
            "NATS server URL (default: NATS_URL env var, then nats://localhost:4222)."
        ),
    ),
    timeout: float = typer.Option(  # noqa: B008
        _DEFAULT_TIMEOUT,
        "--timeout",
        "-t",
        help="Per-request timeout in seconds.",
    ),
    require_voicecli_worker: bool = typer.Option(  # noqa: B008
        False,
        "--require-voicecli-worker",
        help=(
            "Before round-trip, subscribe to heartbeats and require a worker_id "
            f"prefixed {_VOICECLI_WORKER_PREFIX!r} for BOTH STT and TTS. Fails if only "
            "lyra_stt/lyra_tts satellites are answering (silent-cutover guard)."
        ),
    ),
    heartbeat_wait: float = typer.Option(  # noqa: B008
        _DEFAULT_HEARTBEAT_WAIT,
        "--heartbeat-wait",
        help=(
            "Seconds to wait for voicecli-prefixed heartbeats when "
            "--require-voicecli-worker is set."
        ),
    ),
) -> None:
    """Run a TTS→STT round-trip smoke test to verify voicecli NATS workers are up."""
    resolved_url = nats_url or os.environ.get("NATS_URL", _DEFAULT_NATS_URL)
    try:
        asyncio.run(
            _run_smoke(resolved_url, timeout, require_voicecli_worker, heartbeat_wait)
        )
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — resilient: CLI entry-point catch-all for unexpected async errors
        typer.echo(f"FAIL: unexpected error — {exc}", err=True)
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Core async logic
# ---------------------------------------------------------------------------


async def _run_smoke(
    nats_url: str,
    timeout: float,
    require_voicecli_worker: bool = False,
    heartbeat_wait: float = _DEFAULT_HEARTBEAT_WAIT,
) -> None:
    """Execute the round-trip and exit with 0 (pass) or 1 (fail)."""
    try:
        nc = await nats_connect(nats_url)
    except Exception as exc:  # noqa: BLE001 — resilient: NATS connect errors span auth, TLS, DNS, and OS-level failures
        typer.echo(f"FAIL: cannot connect to NATS at {nats_url!r} — {exc}", err=True)
        raise typer.Exit(1)

    try:
        if require_voicecli_worker:
            await _require_voicecli_heartbeats(nc, heartbeat_wait)
        audio_bytes, mime_type = await _step_tts(nc, timeout)
        transcript = await _step_stt(nc, audio_bytes, mime_type, timeout)
        _assert_transcript(transcript)
        typer.echo(f' ok (transcript: "{transcript}")')
        typer.echo("PASS")
    finally:
        await nc.drain()
        await nc.close()


async def _require_voicecli_heartbeats(nc: NATS, wait_seconds: float) -> None:
    """Subscribe to voice heartbeats and require voicecli-prefixed worker_id for both.

    Fails fast if only lyra_stt/lyra_tts satellites are emitting heartbeats —
    the silent-cutover failure mode where ACL misconfiguration leaves voicecli
    connected but non-responsive while lyra satellites keep the smoke green.
    """
    typer.echo(
        f"[0/2] Waiting for voicecli heartbeats (≤ {wait_seconds:.0f}s)...",
        nl=False,
    )
    seen: dict[str, str] = {}

    async def on_tts(msg: object) -> None:
        worker_id = _extract_worker_id(getattr(msg, "data", b""))
        if worker_id and worker_id.startswith(_VOICECLI_WORKER_PREFIX):
            seen["tts"] = worker_id

    async def on_stt(msg: object) -> None:
        worker_id = _extract_worker_id(getattr(msg, "data", b""))
        if worker_id and worker_id.startswith(_VOICECLI_WORKER_PREFIX):
            seen["stt"] = worker_id

    sub_tts = await nc.subscribe(_TTS_HEARTBEAT, cb=on_tts)
    sub_stt = await nc.subscribe(_STT_HEARTBEAT, cb=on_stt)
    try:
        deadline = asyncio.get_event_loop().time() + wait_seconds
        while asyncio.get_event_loop().time() < deadline:
            if "tts" in seen and "stt" in seen:
                break
            await asyncio.sleep(0.25)
    finally:
        await sub_tts.unsubscribe()
        await sub_stt.unsubscribe()

    missing = [side for side in ("tts", "stt") if side not in seen]
    if missing:
        typer.echo("")
        typer.echo(
            f"FAIL: no voicecli-prefixed heartbeat on {missing} within "
            f"{wait_seconds:.0f}s — only lyra satellites are answering "
            f"(observed: {seen or 'none'}). Cutover may be silently incomplete.",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(f" ok (tts={seen['tts']}, stt={seen['stt']})")


def _extract_worker_id(raw: bytes) -> str | None:
    """Best-effort parse of a heartbeat payload — returns None on any decode failure."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    worker_id = data.get("worker_id") if isinstance(data, dict) else None
    return worker_id if isinstance(worker_id, str) else None


async def _step_tts(nc: NATS, timeout: float) -> tuple[bytes, str]:
    """Send TTS request and return (audio_bytes, mime_type).

    Prints progress and raises typer.Exit(1) on any failure.
    """
    typer.echo("[1/2] TTS request...", nl=False)
    payload = json.dumps(
        {
            "contract_version": _CONTRACT_VERSION,
            "request_id": str(uuid4()),
            "text": _SMOKE_TEXT,
            "chunked": True,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    try:
        reply = await nc.request(_TTS_SUBJECT, payload, timeout=timeout)
    except (nats.errors.TimeoutError, TimeoutError):
        typer.echo("")
        typer.echo(
            f"FAIL: TTS request timed out after {timeout:.0f}s (is lyra_tts running?)",
            err=True,
        )
        raise typer.Exit(1)
    except Exception as exc:  # noqa: BLE001 — resilient: NATS request can raise varied errors beyond timeout
        typer.echo("")
        typer.echo(f"FAIL: TTS request error — {exc}", err=True)
        raise typer.Exit(1)

    data = _parse_reply(reply.data, "TTS")
    _assert_ok(data, "TTS synthesis failed")

    audio_b64 = data.get("audio_b64", "")
    if not audio_b64:
        _fail("TTS response missing audio_b64")
    audio_bytes = base64.b64decode(audio_b64)
    if not audio_bytes:
        _fail("TTS returned empty audio_bytes")

    typer.echo(f" ok ({len(audio_bytes)} bytes)")
    return audio_bytes, data.get("mime_type", "audio/ogg")


async def _step_stt(
    nc: NATS, audio_bytes: bytes, mime_type: str, timeout: float
) -> str:
    """Send STT request and return the transcript text.

    Prints progress and raises typer.Exit(1) on any failure.
    """
    typer.echo("[2/2] STT request...", nl=False)
    payload = json.dumps(
        {
            "contract_version": _CONTRACT_VERSION,
            "request_id": str(uuid4()),
            "audio_b64": base64.b64encode(audio_bytes).decode("ascii"),
            "mime_type": mime_type,
            "model": "large-v3-turbo",
        },
        ensure_ascii=False,
    ).encode("utf-8")

    try:
        reply = await nc.request(_STT_SUBJECT, payload, timeout=timeout)
    except (nats.errors.TimeoutError, TimeoutError):
        typer.echo("")
        typer.echo(
            f"FAIL: STT request timed out after {timeout:.0f}s (is lyra_stt running?)",
            err=True,
        )
        raise typer.Exit(1)
    except Exception as exc:  # noqa: BLE001 — resilient: NATS request can raise varied errors beyond timeout
        typer.echo("")
        typer.echo(f"FAIL: STT request error — {exc}", err=True)
        raise typer.Exit(1)

    data = _parse_reply(reply.data, "STT")
    _assert_ok(data, "STT transcription failed")

    transcript = data.get("text", "")
    if not transcript:
        _fail("STT returned empty transcript")
    return transcript


def _assert_transcript(transcript: str) -> None:
    """Check that transcript contains at least one expected keyword."""
    lower = transcript.lower()
    if not any(kw in lower for kw in _SMOKE_KEYWORDS):
        _fail(
            f"transcript mismatch — got {transcript!r}, "
            f"expected at least one of {sorted(_SMOKE_KEYWORDS)}"
        )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _parse_reply(raw: bytes, step: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        typer.echo("")
        typer.echo(f"FAIL: {step} response is not valid JSON — {exc}", err=True)
        raise typer.Exit(1) from exc


def _assert_ok(data: dict, msg: str) -> None:
    if not data.get("ok"):
        typer.echo("")
        detail = data.get("error", "no details")
        typer.echo(f"FAIL: {msg} — {detail}", err=True)
        raise typer.Exit(1)


def _fail(msg: str) -> None:
    """Print a FAIL line to stderr and raise typer.Exit(1)."""
    typer.echo("")
    typer.echo(f"FAIL: {msg}", err=True)
    raise typer.Exit(1)
