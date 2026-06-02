"""Tests for outbound-audio observability (#1482 T11).

Covers:
  (1) audio_terminal_drop_total increments when _handle_terminal fires.
  (2) audio_redelivery_total increments when a message arrives with
      num_delivered > 1.
  (3) check_audio_consumer_lag: happy path (pass), lag-over-threshold (fail),
      stream-absent (pass/skip), HTTP error (fail).
  (4) check_audio_stream_usage: happy path (pass), over-threshold (fail),
      stream-absent (pass/skip), HTTP error (fail).
  (5) Alert thresholds registered in MonitoringConfig with expected defaults.
  (6) run_checks wires both audio checks (presence in results).

Strategy: module-level counters are reset at test start via monkeypatch.
No real NATS server — httpx responses mocked via respx or monkeypatch on
httpx.AsyncClient, consumer interactions use AsyncMock (T5 pattern).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers shared with T5 consumer tests
# ---------------------------------------------------------------------------


def _make_inbound() -> object:
    from factory.core.auth.trust import TrustLevel
    from factory.core.messaging.message import InboundMessage, Platform

    return InboundMessage(
        id="sid-t11",
        platform=Platform.TELEGRAM.value,
        bot_id="123456",
        scope_id="scope:test:1",
        user_id="u:test:1",
        user_name="testuser",
        is_mention=False,
        text="voice",
        text_raw="voice",
        trust_level=TrustLevel.PUBLIC,
    )


def _make_consumer(
    *,
    send_audio: AsyncMock | None = None,
    send_text: AsyncMock | None = None,
) -> object:
    from factory.adapters.nats.jetstream_audio_consumer import JetStreamAudioConsumer

    js = MagicMock()
    return JetStreamAudioConsumer(
        js,
        durable="outbound-audio-telegram",
        filter_subject="lyra.outbound.audio.telegram.>",
        send_audio=send_audio or AsyncMock(),
        send_text=send_text or AsyncMock(),
    )


def _make_nats_msg(
    *,
    stream_id: str = "sid-t11",
    num_delivered_val: int = 1,
) -> MagicMock:
    import json

    from factory.core.auth.trust import TrustLevel
    from factory.core.messaging.message import InboundMessage, OutboundAudio, Platform
    from roxabi_contracts.blob_ref import BlobRef
    from roxabi_nats._serialize import serialize

    audio = OutboundAudio(
        blob_ref=BlobRef(
            store_key="key/t11.ogg",
            content_hash="abc",
            mime="audio/ogg",
            size=512,
            source="voicecli",
        ),
        mime_type="audio/ogg",
    )
    inbound = InboundMessage(
        id=stream_id,
        platform=Platform.TELEGRAM.value,
        bot_id="123456",
        scope_id="scope:test:1",
        user_id="u:test:1",
        user_name="testuser",
        is_mention=False,
        text="voice",
        text_raw="voice",
        trust_level=TrustLevel.PUBLIC,
    )
    envelope = {
        "type": "audio",
        "stream_id": stream_id,
        "audio": json.loads(serialize(audio).decode()),
        "original_msg": json.loads(serialize(inbound).decode()),
    }
    msg = MagicMock()
    msg.data = json.dumps(envelope).encode()
    msg.subject = f"lyra.outbound.audio.telegram.{inbound.bot_id}"
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    msg.term = AsyncMock()
    meta = MagicMock()
    meta.num_delivered = num_delivered_val
    msg.metadata = meta
    return msg


# ===========================================================================
# (1) audio_terminal_drop_total counter
# ===========================================================================


@pytest.mark.anyio
async def test_terminal_drop_counter_increments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """audio_terminal_drop_total increments by 1 each time _handle_terminal fires."""
    import factory.adapters.nats.jetstream_audio_consumer as mod

    monkeypatch.setattr(mod, "audio_terminal_drop_total", 0)

    consumer = _make_consumer(send_text=AsyncMock())
    msg = MagicMock()
    msg.term = AsyncMock()
    inbound = _make_inbound()

    await consumer._handle_terminal(msg, "sid-term-a", inbound)  # type: ignore[attr-defined]
    assert mod.audio_terminal_drop_total == 1

    # Second distinct stream_id → second increment
    msg2 = MagicMock()
    msg2.term = AsyncMock()
    await consumer._handle_terminal(msg2, "sid-term-b", inbound)  # type: ignore[attr-defined]
    assert mod.audio_terminal_drop_total == 2


@pytest.mark.anyio
async def test_terminal_drop_counter_increments_via_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counter increments when _process routes to _handle_terminal."""
    import factory.adapters.nats.jetstream_audio_consumer as mod
    from factory.adapters.nats.jetstream_audio_consumer import MAX_DELIVER

    monkeypatch.setattr(mod, "audio_terminal_drop_total", 0)

    send_audio = AsyncMock(side_effect=RuntimeError("platform down"))
    consumer = _make_consumer(send_audio=send_audio, send_text=AsyncMock())
    msg = _make_nats_msg(stream_id="sid-proc-term", num_delivered_val=MAX_DELIVER)

    await consumer._process(msg)  # type: ignore[attr-defined]
    assert mod.audio_terminal_drop_total == 1


@pytest.mark.anyio
async def test_terminal_drop_counter_not_incremented_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counter does NOT increment on a successful send."""
    import factory.adapters.nats.jetstream_audio_consumer as mod

    monkeypatch.setattr(mod, "audio_terminal_drop_total", 0)

    consumer = _make_consumer(send_audio=AsyncMock())
    msg = _make_nats_msg(stream_id="sid-success")
    await consumer._process(msg)  # type: ignore[attr-defined]
    assert mod.audio_terminal_drop_total == 0


# ===========================================================================
# (2) audio_redelivery_total counter
# ===========================================================================


@pytest.mark.anyio
async def test_redelivery_counter_increments_on_second_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """audio_redelivery_total increments when num_delivered > 1."""
    import factory.adapters.nats.jetstream_audio_consumer as mod

    monkeypatch.setattr(mod, "audio_redelivery_total", 0)

    consumer = _make_consumer(
        send_audio=AsyncMock(side_effect=OSError("transient")),
    )
    msg = _make_nats_msg(stream_id="sid-redeliver", num_delivered_val=2)
    await consumer._process(msg)  # type: ignore[attr-defined]
    assert mod.audio_redelivery_total == 1


@pytest.mark.anyio
async def test_redelivery_counter_not_incremented_on_first_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """audio_redelivery_total does NOT increment for first delivery."""
    import factory.adapters.nats.jetstream_audio_consumer as mod

    monkeypatch.setattr(mod, "audio_redelivery_total", 0)

    consumer = _make_consumer(send_audio=AsyncMock())
    msg = _make_nats_msg(stream_id="sid-first", num_delivered_val=1)
    await consumer._process(msg)  # type: ignore[attr-defined]
    assert mod.audio_redelivery_total == 0


# ===========================================================================
# (3) check_audio_consumer_lag
# ===========================================================================

_STREAM_JSZ = {
    "streams": [
        {
            "name": "LYRA_OUTBOUND_AUDIO",
            "config": {"max_bytes": 33554432},
            "state": {"bytes": 1024},
            "consumers": [
                {"name": "outbound-audio-telegram", "num_pending": 5},
                {"name": "outbound-audio-discord", "num_pending": 3},
            ],
        }
    ]
}


@pytest.mark.anyio
async def test_consumer_lag_passes_below_threshold() -> None:
    """num_pending=5 < threshold=50 → passed=True."""
    from factory.monitoring.checks_audio import check_audio_consumer_lag

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _STREAM_JSZ

    with patch("factory.monitoring.checks_audio.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await check_audio_consumer_lag(
            "http://127.0.0.1:8222", lag_pending_threshold=50
        )

    assert result.name == "audio:consumer_lag"
    assert result.passed is True
    assert "pending=5" in result.detail
    assert "pending=3" in result.detail


@pytest.mark.anyio
async def test_consumer_lag_fails_above_threshold() -> None:
    """num_pending=80 > threshold=50 → passed=False."""
    from factory.monitoring.checks_audio import check_audio_consumer_lag

    data = {
        "streams": [
            {
                "name": "LYRA_OUTBOUND_AUDIO",
                "config": {},
                "state": {},
                "consumers": [{"name": "outbound-audio-telegram", "num_pending": 80}],
            }
        ]
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = data

    with patch("factory.monitoring.checks_audio.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await check_audio_consumer_lag(
            "http://127.0.0.1:8222", lag_pending_threshold=50
        )

    assert result.passed is False
    assert "num_pending=80" in result.detail
    assert "threshold=50" in result.detail


@pytest.mark.anyio
async def test_consumer_lag_skips_when_stream_absent() -> None:
    """404 from /jsz → passed=True (stream not yet provisioned)."""
    from factory.monitoring.checks_audio import check_audio_consumer_lag

    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch("factory.monitoring.checks_audio.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await check_audio_consumer_lag("http://127.0.0.1:8222")

    assert result.passed is True
    assert "not yet provisioned" in result.detail


@pytest.mark.anyio
async def test_consumer_lag_fails_on_http_error() -> None:
    """Connection error → passed=False (detail is exc type name, not message)."""
    from factory.monitoring.checks_audio import check_audio_consumer_lag

    with patch("factory.monitoring.checks_audio.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=OSError("connection refused"))
        mock_client_cls.return_value = mock_client

        result = await check_audio_consumer_lag("http://127.0.0.1:8222")

    assert result.passed is False
    # SanitizedError discipline: detail is the exception class name, not the message
    assert result.detail == "OSError"


# ---------------------------------------------------------------------------
# lag-age check tests (#1482 B2)
# ---------------------------------------------------------------------------


def _make_lag_jsz(
    *,
    num_pending: int,
    last_active: str,
) -> dict:
    """Build a minimal /jsz payload with ack_floor.last_active set."""
    return {
        "streams": [
            {
                "name": "LYRA_OUTBOUND_AUDIO",
                "config": {"max_bytes": 33554432},
                "state": {"bytes": 1024},
                "consumers": [
                    {
                        "name": "outbound-audio-telegram",
                        "num_pending": num_pending,
                        "ack_floor": {"last_active": last_active},
                    }
                ],
            }
        ]
    }


@pytest.mark.anyio
async def test_consumer_lag_age_warn_when_old_last_active() -> None:
    """pending>0 + last_active older than lag_age_warn_s → passed=False."""
    from datetime import datetime, timedelta, timezone

    from factory.monitoring.checks_audio import check_audio_consumer_lag

    # 25 hours ago — exceeds the 72000s (20h) default threshold
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    data = _make_lag_jsz(num_pending=3, last_active=old_ts)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = data

    with patch("factory.monitoring.checks_audio.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await check_audio_consumer_lag(
            "http://127.0.0.1:8222", lag_age_warn_s=72000
        )

    assert result.passed is False
    assert "oldest unacked age" in result.detail
    assert "warn_threshold=72000s" in result.detail


@pytest.mark.anyio
async def test_consumer_lag_age_ok_when_recent_last_active() -> None:
    """pending>0 + last_active within lag_age_warn_s → passed=True."""
    from datetime import datetime, timedelta, timezone

    from factory.monitoring.checks_audio import check_audio_consumer_lag

    # 5 minutes ago — well within the 20h threshold
    recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    data = _make_lag_jsz(num_pending=3, last_active=recent_ts)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = data

    with patch("factory.monitoring.checks_audio.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await check_audio_consumer_lag(
            "http://127.0.0.1:8222", lag_age_warn_s=72000
        )

    assert result.passed is True


@pytest.mark.anyio
async def test_consumer_lag_age_ok_when_no_pending() -> None:
    """pending==0 → age check skipped → passed=True regardless of last_active."""
    from datetime import datetime, timedelta, timezone

    from factory.monitoring.checks_audio import check_audio_consumer_lag

    # Very old timestamp — but pending=0 so the age check must not fire
    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    data = _make_lag_jsz(num_pending=0, last_active=old_ts)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = data

    with patch("factory.monitoring.checks_audio.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await check_audio_consumer_lag(
            "http://127.0.0.1:8222", lag_age_warn_s=72000
        )

    assert result.passed is True


# ===========================================================================
# (4) check_audio_stream_usage
# ===========================================================================


@pytest.mark.anyio
async def test_stream_usage_passes_below_threshold() -> None:
    """10% usage < 80% threshold → passed=True."""
    from factory.monitoring.checks_audio import check_audio_stream_usage

    data = {
        "streams": [
            {
                "name": "LYRA_OUTBOUND_AUDIO",
                "config": {"max_bytes": 33554432},
                "state": {"bytes": 3355443},  # ~10%
            }
        ]
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = data

    with patch("factory.monitoring.checks_audio.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await check_audio_stream_usage("http://127.0.0.1:8222", warn_pct=80)

    assert result.name == "audio:stream_usage"
    assert result.passed is True
    assert "OK" in result.detail


@pytest.mark.anyio
async def test_stream_usage_fails_above_threshold() -> None:
    """90% usage > 80% threshold → passed=False."""
    from factory.monitoring.checks_audio import check_audio_stream_usage

    max_b = 33554432
    used_b = int(max_b * 0.90)
    data = {
        "streams": [
            {
                "name": "LYRA_OUTBOUND_AUDIO",
                "config": {"max_bytes": max_b},
                "state": {"bytes": used_b},
            }
        ]
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = data

    with patch("factory.monitoring.checks_audio.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await check_audio_stream_usage("http://127.0.0.1:8222", warn_pct=80)

    assert result.passed is False
    assert "WARNING" in result.detail
    assert "threshold=80%" in result.detail


@pytest.mark.anyio
async def test_stream_usage_uses_fallback_max_bytes() -> None:
    """When config.max_bytes absent, fallback 32 MiB constant used."""
    from factory.monitoring.checks_audio import (
        _FALLBACK_MAX_BYTES,
        check_audio_stream_usage,
    )

    data = {
        "streams": [
            {
                "name": "LYRA_OUTBOUND_AUDIO",
                "config": {},  # no max_bytes
                "state": {"bytes": 100},
            }
        ]
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = data

    with patch("factory.monitoring.checks_audio.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await check_audio_stream_usage("http://127.0.0.1:8222", warn_pct=80)

    assert result.passed is True
    assert str(_FALLBACK_MAX_BYTES) in result.detail


@pytest.mark.anyio
async def test_stream_usage_skips_when_stream_absent() -> None:
    """404 from /jsz → passed=True (stream not yet provisioned)."""
    from factory.monitoring.checks_audio import check_audio_stream_usage

    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch("factory.monitoring.checks_audio.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await check_audio_stream_usage("http://127.0.0.1:8222")

    assert result.passed is True
    assert "not yet provisioned" in result.detail


# ===========================================================================
# (5) MonitoringConfig: alert threshold defaults
# ===========================================================================


def test_monitoring_config_audio_threshold_defaults() -> None:
    """MonitoringConfig exposes audio thresholds with correct defaults."""
    from factory.monitoring.config import MonitoringConfig

    cfg = MonitoringConfig(telegram_token="t", telegram_admin_chat_id="1")
    assert cfg.audio_lag_pending_threshold == 50
    assert cfg.audio_lag_age_warn_s == 72000  # 20 h
    assert cfg.audio_stream_usage_warn_pct == 80


def test_monitoring_config_audio_thresholds_overrideable() -> None:
    """Audio thresholds can be overridden via TOML-style kwargs."""
    from factory.monitoring.config import MonitoringConfig

    cfg = MonitoringConfig(
        telegram_token="t",
        telegram_admin_chat_id="1",
        audio_lag_pending_threshold=10,
        audio_lag_age_warn_s=3600,
        audio_stream_usage_warn_pct=60,
    )
    assert cfg.audio_lag_pending_threshold == 10
    assert cfg.audio_lag_age_warn_s == 3600
    assert cfg.audio_stream_usage_warn_pct == 60


# ===========================================================================
# (6) run_checks wires audio checks (check names present in results)
# ===========================================================================


@pytest.mark.anyio
async def test_run_checks_includes_audio_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_checks result set includes audio:consumer_lag and audio:stream_usage."""
    from factory.monitoring.checks import run_checks
    from factory.monitoring.config import MonitoringConfig

    config = MonitoringConfig(
        telegram_token="t",
        telegram_admin_chat_id="1",
        health_endpoint_url="http://localhost:8443/health",
        service_names=["lyra-hub"],
    )

    # Mock systemctl
    monkeypatch.setattr(
        "factory.monitoring.checks.subprocess.run",
        MagicMock(return_value=MagicMock(returncode=0, stdout="active\n")),
    )
    # Mock podman logs (log-scan checks)
    monkeypatch.setattr(
        "factory.monitoring.checks_log.subprocess.run",
        MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr="")),
    )

    # Mock HTTP health endpoint
    health_resp = MagicMock()
    health_resp.status_code = 200
    health_resp.json.return_value = {
        "queue_size": 0,
        "circuits": {},
    }

    # Mock NATS varz (404 → first-run baseline; handled by check_nats_varz)
    varz_resp = MagicMock()
    varz_resp.status_code = 200
    varz_resp.json.return_value = {"auth_errors": 0, "slow_consumers": 0}

    # Mock /jsz for audio checks → 404 (stream not yet provisioned → pass)
    jsz_resp = MagicMock()
    jsz_resp.status_code = 404

    call_count: dict[str, int] = {"n": 0}

    async def _mock_get(url: str, **_kwargs: object) -> MagicMock:
        call_count["n"] += 1
        if "/varz" in url:
            return varz_resp
        if "/jsz" in url:
            return jsz_resp
        return health_resp

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = _mock_get

    with (
        patch("factory.monitoring.checks.httpx.AsyncClient", return_value=mock_client),
        patch(
            "factory.monitoring.checks_varz.httpx.AsyncClient", return_value=mock_client
        ),
        patch(
            "factory.monitoring.checks_audio.httpx.AsyncClient",
            return_value=mock_client,
        ),
    ):
        report = await run_checks(config)

    check_names = {c.name for c in report.checks}
    assert "audio:consumer_lag" in check_names, (
        f"missing audio:consumer_lag in {check_names}"
    )
    assert "audio:stream_usage" in check_names, (
        f"missing audio:stream_usage in {check_names}"
    )
