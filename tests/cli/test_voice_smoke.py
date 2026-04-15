"""Tests for `lyra voice-smoke` CLI subcommand (issue #689, T4).

Covers:
  - Happy path: TTS+STT succeed, transcript contains a keyword → exit 0
  - TTS failure: TTS response ok=False → exit 1
  - STT failure: STT response ok=False → exit 1
  - Transcript mismatch: no expected keyword in transcript → exit 1
  - TTS timeout: asyncio.wait_for raises TimeoutError → exit 1
  - STT timeout: asyncio.wait_for raises TimeoutError → exit 1
"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from lyra.cli import lyra_app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_AUDIO = b"FAKE_AUDIO_BYTES"
_FAKE_AUDIO_B64 = base64.b64encode(_FAKE_AUDIO).decode("ascii")


def _nats_reply(data: dict) -> SimpleNamespace:
    """Build a fake NATS reply message."""
    return SimpleNamespace(data=json.dumps(data).encode("utf-8"))


def _tts_ok_response() -> dict:
    return {"ok": True, "audio_b64": _FAKE_AUDIO_B64, "mime_type": "audio/ogg"}


def _stt_ok_response(text: str = "one two three") -> dict:
    return {
        "ok": True,
        "text": text,
        "language": "en",
        "duration_seconds": 1.5,
    }


def _make_nc_mock(
    tts_response: dict,
    stt_response: dict,
    heartbeat_workers: dict[str, str] | None = None,
) -> AsyncMock:
    """Return a mock NATS client returning given responses in order.

    heartbeat_workers: optional {"tts": "voicecli-...", "stt": "voicecli-..."}
    dispatch to subscribe callbacks immediately when --require-voicecli-worker is set.
    """
    nc = AsyncMock()
    nc.request = AsyncMock(
        side_effect=[
            _nats_reply(tts_response),
            _nats_reply(stt_response),
        ]
    )
    nc.drain = AsyncMock()
    nc.close = AsyncMock()

    async def subscribe(subject: str, cb=None, **_kwargs):
        sub = AsyncMock()
        sub.unsubscribe = AsyncMock()
        if heartbeat_workers and cb is not None:
            side = "tts" if "tts" in subject else "stt"
            if side in heartbeat_workers:
                payload = json.dumps({"worker_id": heartbeat_workers[side]}).encode()
                await cb(SimpleNamespace(data=payload))
        return sub

    nc.subscribe = AsyncMock(side_effect=subscribe)
    return nc


@pytest.fixture(autouse=True)
def restore_event_loop():
    """Restore a fresh event loop after each test (CLI calls asyncio.run internally).

    Note: the old loop is already closed by asyncio.run() before we get here.
    We only need to install a fresh one so asyncio.get_event_loop() works for
    any code that peeks at the current loop between tests.
    """
    yield
    asyncio.set_event_loop(asyncio.new_event_loop())


# ---------------------------------------------------------------------------
# Patch helpers
# ---------------------------------------------------------------------------


def _patch_nats(nc_mock: AsyncMock):
    """Patch nats_connect to return the given mock NATS client."""
    return patch(
        "lyra.cli_voice_smoke.nats_connect",
        new=AsyncMock(return_value=nc_mock),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestVoiceSmokeHappyPath:
    def test_exits_zero_on_success(self) -> None:
        """Happy path: TTS and STT both succeed, transcript has a keyword."""
        nc = _make_nc_mock(_tts_ok_response(), _stt_ok_response("one two three"))

        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke"])

        assert result.exit_code == 0, (
            f"Expected 0, got {result.exit_code}:\n{result.output}"
        )
        assert "[1/2] TTS request" in result.output
        assert "[2/2] STT request" in result.output
        assert "PASS" in result.output

    def test_prints_audio_byte_count(self) -> None:
        """Output includes the number of audio bytes from TTS."""
        nc = _make_nc_mock(_tts_ok_response(), _stt_ok_response("voice test"))

        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke"])

        assert str(len(_FAKE_AUDIO)) in result.output

    def test_prints_transcript(self) -> None:
        """Output includes the transcript returned by STT."""
        nc = _make_nc_mock(_tts_ok_response(), _stt_ok_response("voice cutover ok"))

        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke"])

        assert "voice cutover ok" in result.output

    def test_keyword_matching_case_insensitive(self) -> None:
        """Keyword match is case-insensitive (ASR may uppercase)."""
        nc = _make_nc_mock(_tts_ok_response(), _stt_ok_response("ONE TWO THREE"))

        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke"])

        assert result.exit_code == 0

    def test_nats_url_flag_overrides_default(self) -> None:
        """--nats-url flag is passed through to nats_connect."""
        nc = _make_nc_mock(_tts_ok_response(), _stt_ok_response("one"))

        with patch(
            "lyra.cli_voice_smoke.nats_connect", new=AsyncMock(return_value=nc)
        ) as mock_connect:
            result = runner.invoke(
                lyra_app, ["voice-smoke", "--nats-url", "nats://myserver:4222"]
            )

        mock_connect.assert_called_once_with("nats://myserver:4222")
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# TTS failure
# ---------------------------------------------------------------------------


class TestVoiceSmokeTtsFailure:
    def test_exits_one_on_tts_ok_false(self) -> None:
        """TTS response with ok=False → exit 1."""
        tts_fail = {"ok": False, "error": "TTS worker crashed"}
        nc = AsyncMock()
        nc.request = AsyncMock(return_value=_nats_reply(tts_fail))
        nc.drain = AsyncMock()

        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke"])

        assert result.exit_code == 1

    def test_exits_one_on_tts_empty_audio(self) -> None:
        """TTS response with empty audio_b64 → exit 1."""
        tts_empty = {"ok": True, "audio_b64": "", "mime_type": "audio/ogg"}
        nc = AsyncMock()
        nc.request = AsyncMock(return_value=_nats_reply(tts_empty))
        nc.drain = AsyncMock()

        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke"])

        assert result.exit_code == 1

    def test_exits_one_on_tts_timeout(self) -> None:
        """TTS request times out → exit 1 with message mentioning lyra_tts."""
        nc = AsyncMock()
        nc.request = AsyncMock(side_effect=TimeoutError())
        nc.drain = AsyncMock()

        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke", "--timeout", "5"])

        assert result.exit_code == 1
        assert "lyra_tts" in result.output or "TTS" in result.output

    def test_timeout_flag_value_is_used(self) -> None:
        """--timeout value is forwarded to asyncio.wait_for (verifiable via message)."""
        nc = AsyncMock()
        nc.request = AsyncMock(side_effect=TimeoutError())
        nc.drain = AsyncMock()

        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke", "--timeout", "7"])

        # The error message includes the timeout value
        assert "7" in result.output


# ---------------------------------------------------------------------------
# STT failure
# ---------------------------------------------------------------------------


class TestVoiceSmokeSttFailure:
    def test_exits_one_on_stt_ok_false(self) -> None:
        """STT response with ok=False → exit 1."""
        stt_fail = {"ok": False, "error": "STT worker error"}
        nc = AsyncMock()
        nc.request = AsyncMock(
            side_effect=[
                _nats_reply(_tts_ok_response()),
                _nats_reply(stt_fail),
            ]
        )
        nc.drain = AsyncMock()

        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke"])

        assert result.exit_code == 1

    def test_exits_one_on_stt_empty_transcript(self) -> None:
        """STT response with empty text → exit 1."""
        stt_empty = {"ok": True, "text": "", "language": "en", "duration_seconds": 0.0}
        nc = AsyncMock()
        nc.request = AsyncMock(
            side_effect=[
                _nats_reply(_tts_ok_response()),
                _nats_reply(stt_empty),
            ]
        )
        nc.drain = AsyncMock()

        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke"])

        assert result.exit_code == 1

    def test_exits_one_on_stt_timeout(self) -> None:
        """STT request times out → exit 1 with message mentioning lyra_stt."""
        nc = AsyncMock()

        async def _tts_then_timeout(subject, payload, *args, **kwargs):  # noqa: ANN001
            if subject == "lyra.voice.tts.request":
                return _nats_reply(_tts_ok_response())
            raise TimeoutError()

        nc.request = AsyncMock(side_effect=_tts_then_timeout)
        nc.drain = AsyncMock()

        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke"])

        assert result.exit_code == 1
        assert "lyra_stt" in result.output or "STT" in result.output


# ---------------------------------------------------------------------------
# Transcript mismatch
# ---------------------------------------------------------------------------


class TestVoiceSmokeMismatch:
    def test_exits_one_on_transcript_mismatch(self) -> None:
        """Transcript with no expected keywords → exit 1."""
        nc = _make_nc_mock(_tts_ok_response(), _stt_ok_response("hello world"))

        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke"])

        assert result.exit_code == 1
        assert "mismatch" in result.output.lower() or "FAIL" in result.output

    def test_single_keyword_match_is_sufficient(self) -> None:
        """A transcript containing just one expected keyword passes."""
        nc = _make_nc_mock(_tts_ok_response(), _stt_ok_response("voice"))

        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke"])

        assert result.exit_code == 0

    def test_all_keywords_accepted(self) -> None:
        """Each of the expected keywords is sufficient on its own."""
        keywords = ["voice", "cutover", "smoke", "one", "two", "three"]
        for kw in keywords:
            nc = _make_nc_mock(_tts_ok_response(), _stt_ok_response(kw))
            with _patch_nats(nc):
                result = runner.invoke(lyra_app, ["voice-smoke"])
            assert result.exit_code == 0, (
                f"keyword {kw!r} should pass but got exit {result.exit_code}"
            )


# ---------------------------------------------------------------------------
# NATS connection failure
# ---------------------------------------------------------------------------


class TestVoiceSmokeConnectionFailure:
    def test_exits_one_on_nats_connection_error(self) -> None:
        """nats_connect raising an exception → exit 1."""
        with patch(
            "lyra.cli_voice_smoke.nats_connect",
            new=AsyncMock(side_effect=Exception("connection refused")),
        ):
            result = runner.invoke(lyra_app, ["voice-smoke"])

        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Help output
# ---------------------------------------------------------------------------


class TestVoiceSmokeHelp:
    def test_help_flag_shows_command(self) -> None:
        """--help outputs something about the round-trip smoke test."""
        result = runner.invoke(lyra_app, ["voice-smoke", "--help"])

        assert result.exit_code == 0
        assert "smoke" in result.output.lower() or "TTS" in result.output


# ---------------------------------------------------------------------------
# --require-voicecli-worker flag
# ---------------------------------------------------------------------------


class TestRequireVoicecliWorker:
    """--require-voicecli-worker asserts voicecli-prefixed worker_id on heartbeats."""

    def test_passes_when_both_voicecli_heartbeats_seen(self) -> None:
        """Both heartbeats from voicecli-* workers → exit 0 + round-trip succeeds."""
        nc = _make_nc_mock(
            _tts_ok_response(),
            _stt_ok_response(),
            heartbeat_workers={
                "tts": "voicecli-host-1234",
                "stt": "voicecli-host-5678",
            },
        )
        with _patch_nats(nc):
            result = runner.invoke(
                lyra_app,
                ["voice-smoke", "--require-voicecli-worker", "--heartbeat-wait", "1"],
            )
        assert result.exit_code == 0
        assert "voicecli-host-1234" in result.output
        assert "voicecli-host-5678" in result.output

    def test_fails_when_no_heartbeats_seen(self) -> None:
        """No voicecli heartbeats within the wait window → exit 1, no round-trip."""
        nc = _make_nc_mock(
            _tts_ok_response(),
            _stt_ok_response(),
            heartbeat_workers=None,
        )
        with _patch_nats(nc):
            result = runner.invoke(
                lyra_app,
                ["voice-smoke", "--require-voicecli-worker", "--heartbeat-wait", "0.5"],
            )
        assert result.exit_code == 1
        assert "no voicecli-prefixed heartbeat" in result.output

    def test_fails_when_only_tts_heartbeat_seen(self) -> None:
        """Missing STT voicecli heartbeat → exit 1 (silent-cutover failure mode)."""
        nc = _make_nc_mock(
            _tts_ok_response(),
            _stt_ok_response(),
            heartbeat_workers={"tts": "voicecli-host-tts"},
        )
        with _patch_nats(nc):
            result = runner.invoke(
                lyra_app,
                ["voice-smoke", "--require-voicecli-worker", "--heartbeat-wait", "0.5"],
            )
        assert result.exit_code == 1
        assert "['stt']" in result.output or "stt" in result.output

    def test_fails_when_only_lyra_satellite_heartbeat_seen(self) -> None:
        """Non-voicecli-prefixed worker_id (lyra satellite) doesn't count."""
        # Simulate lyra_stt emitting a heartbeat with worker_id=lyra-stt-pid123.
        nc = AsyncMock()
        nc.drain = AsyncMock()
        nc.close = AsyncMock()

        async def subscribe(subject: str, cb=None, **_kwargs):
            sub = AsyncMock()
            sub.unsubscribe = AsyncMock()
            if cb is not None:
                # lyra-prefixed worker_id — not voicecli-.
                payload = json.dumps({"worker_id": "lyra-stt-pid123"}).encode()
                await cb(SimpleNamespace(data=payload))
            return sub

        nc.subscribe = AsyncMock(side_effect=subscribe)

        with _patch_nats(nc):
            result = runner.invoke(
                lyra_app,
                ["voice-smoke", "--require-voicecli-worker", "--heartbeat-wait", "0.5"],
            )
        assert result.exit_code == 1
        assert "no voicecli-prefixed heartbeat" in result.output

    def test_skipped_when_flag_not_set(self) -> None:
        """Without --require-voicecli-worker, no subscribe happens."""
        nc = _make_nc_mock(_tts_ok_response(), _stt_ok_response())
        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke"])
        assert result.exit_code == 0
        # subscribe is not called on the default path.
        nc.subscribe.assert_not_called()


# ---------------------------------------------------------------------------
# Generic exception and malformed JSON branches (coverage gaps)
# ---------------------------------------------------------------------------


class TestGenericExceptionBranches:
    """Exercise the `except Exception` fallbacks and malformed-JSON `_parse_reply`."""

    def test_tts_generic_exception_exits_one(self) -> None:
        """nc.request raising a non-timeout error on TTS → exit 1."""
        nc = AsyncMock()
        nc.drain = AsyncMock()
        nc.close = AsyncMock()
        nc.request = AsyncMock(side_effect=RuntimeError("boom"))
        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke"])
        assert result.exit_code == 1
        assert "TTS request error" in result.output
        assert "boom" in result.output

    def test_stt_generic_exception_exits_one(self) -> None:
        """TTS succeeds, STT nc.request raises non-timeout → exit 1."""
        nc = AsyncMock()
        nc.drain = AsyncMock()
        nc.close = AsyncMock()
        nc.request = AsyncMock(
            side_effect=[
                _nats_reply(_tts_ok_response()),
                RuntimeError("stt-crash"),
            ]
        )
        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke"])
        assert result.exit_code == 1
        assert "STT request error" in result.output
        assert "stt-crash" in result.output

    def test_tts_malformed_json_reply_exits_one(self) -> None:
        """TTS reply is not valid JSON → exit 1 with parse error message."""
        nc = AsyncMock()
        nc.drain = AsyncMock()
        nc.close = AsyncMock()
        nc.request = AsyncMock(return_value=SimpleNamespace(data=b"not-json"))
        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke"])
        assert result.exit_code == 1
        assert "TTS response is not valid JSON" in result.output

    def test_stt_malformed_json_reply_exits_one(self) -> None:
        """TTS ok, STT reply is not valid JSON → exit 1 with parse error message."""
        nc = AsyncMock()
        nc.drain = AsyncMock()
        nc.close = AsyncMock()
        nc.request = AsyncMock(
            side_effect=[
                _nats_reply(_tts_ok_response()),
                SimpleNamespace(data=b"<garbage>"),
            ]
        )
        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke"])
        assert result.exit_code == 1
        assert "STT response is not valid JSON" in result.output


# ---------------------------------------------------------------------------
# NATS_URL env-var fallback and ok=False detail propagation
# ---------------------------------------------------------------------------


class TestNatsUrlEnvFallback:
    def test_uses_nats_url_env_when_flag_absent(self, monkeypatch) -> None:
        """No --nats-url, NATS_URL env set → that URL is passed to nats_connect."""
        monkeypatch.setenv("NATS_URL", "nats://envhost:4222")
        nc = _make_nc_mock(_tts_ok_response(), _stt_ok_response())
        connect_mock = AsyncMock(return_value=nc)
        with patch("lyra.cli_voice_smoke.nats_connect", new=connect_mock):
            result = runner.invoke(lyra_app, ["voice-smoke"])
        assert result.exit_code == 0
        # First positional arg to nats_connect is the URL.
        connect_mock.assert_awaited_once()
        assert connect_mock.await_args is not None
        assert connect_mock.await_args.args[0] == "nats://envhost:4222"


class TestOkFalseErrorDetailPropagation:
    def test_tts_ok_false_propagates_error_field(self) -> None:
        """TTS ok=False → error field detail appears in stderr output."""
        nc = _make_nc_mock(
            {"ok": False, "error": "TTS worker crashed"},
            _stt_ok_response(),
        )
        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke"])
        assert result.exit_code == 1
        assert "TTS worker crashed" in result.output

    def test_stt_ok_false_propagates_error_field(self) -> None:
        """STT ok=False → error field detail appears in stderr output."""
        nc = _make_nc_mock(
            _tts_ok_response(),
            {"ok": False, "error": "whisper OOM"},
        )
        with _patch_nats(nc):
            result = runner.invoke(lyra_app, ["voice-smoke"])
        assert result.exit_code == 1
        assert "whisper OOM" in result.output
