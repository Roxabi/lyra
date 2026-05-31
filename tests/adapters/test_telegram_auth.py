"""Tests for Telegram adapter inbound path and Hub-side auth gate (C3).

After C3 (trust re-resolution #456), adapters forward all messages with
trust_level=PUBLIC to the bus; the Hub resolves trust and TrustGuardMiddleware
drops BLOCKED users. These tests verify the adapter-side half of that contract.

Covers: T2 (missing secret → 401), T9 (missing env var → SystemExit),
SC-14 (GET /status returns all circuits), C3 (adapter forwards with PUBLIC trust).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.auth.trust import TrustLevel
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry

# ---------------------------------------------------------------------------
# File-local helpers
# ---------------------------------------------------------------------------


def _make_aiogram_msg(user_id: int = 42) -> object:
    """Build a minimal aiogram-like message SimpleNamespace."""
    return SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=user_id, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=1,
        entities=None,
    )


# ---------------------------------------------------------------------------
# T2 — Missing secret token → HTTP 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_secret_returns_401() -> None:
    """POST /webhooks/telegram/main without X-Telegram-Bot-Api-Secret-Token → 401."""
    import httpx

    from lyra.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        inbound_bus=MagicMock(),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=adapter.app)
    ) as client:
        response = await client.post(
            "/webhooks/telegram/main",
            json={"update_id": 1},
        )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# T9 — Missing TELEGRAM_TOKEN env var → SystemExit
# ---------------------------------------------------------------------------


def test_missing_token_raises_on_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_config() raises SystemExit with 'TELEGRAM_TOKEN' when env var is absent."""
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)

    from lyra.config import load_config

    with pytest.raises(SystemExit, match="TELEGRAM_TOKEN"):
        load_config()


# ---------------------------------------------------------------------------
# SC-14 — GET /status returns all 4 circuit states
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_status_endpoint_returns_all_circuits() -> None:
    """SC-14: GET /status → JSON with all 4 circuit states."""
    import httpx

    from lyra.adapters.telegram import TelegramAdapter

    registry = CircuitRegistry()
    for name in ("claude-cli", "telegram", "discord", "hub"):
        registry.register(
            CircuitBreaker(name, failure_threshold=3, recovery_timeout=60)
        )

    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        inbound_bus=MagicMock(),
        webhook_secret="secret",
        circuit_registry=registry,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=adapter.app)
    ) as client:
        response = await client.get(
            "/status",
            headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "services" in data
    services = data["services"]
    for name in ("claude-cli", "telegram", "discord", "hub"):
        assert name in services, f"Missing circuit '{name}' in /status response"
        assert "state" in services[name]


# ---------------------------------------------------------------------------
# C3: Adapter always forwards with PUBLIC trust — Hub resolves trust
# ---------------------------------------------------------------------------


class TestTelegramAdapterInbound:
    """C3 contract: adapter forwards all non-bot messages with raw PUBLIC trust."""

    @pytest.mark.asyncio
    async def test_any_user_forwarded_with_public_trust(self) -> None:
        """All users reach the bus with trust_level=PUBLIC (Hub resolves trust)."""
        from lyra.adapters.telegram import TelegramAdapter

        inbound_bus = MagicMock()
        inbound_bus.put = AsyncMock()
        adapter = TelegramAdapter(
            bot_id="main",
            token="tok",
            inbound_bus=inbound_bus,
        )
        adapter.bot = AsyncMock()

        await adapter._on_message(_make_aiogram_msg())

        inbound_bus.put.assert_awaited_once()
        _platform, msg = inbound_bus.put.call_args[0]
        assert msg.trust_level == TrustLevel.PUBLIC
        assert msg.is_admin is False

    @pytest.mark.asyncio
    async def test_bot_message_still_dropped_early(self) -> None:
        """Bot-authored messages are filtered before reaching the bus."""
        from unittest.mock import patch

        from lyra.adapters.telegram import TelegramAdapter

        inbound_bus = MagicMock()
        inbound_bus.put = AsyncMock()
        adapter = TelegramAdapter(
            bot_id="main",
            token="tok",
            inbound_bus=inbound_bus,
        )

        bot_msg = SimpleNamespace(
            chat=SimpleNamespace(id=123, type="private"),
            from_user=SimpleNamespace(id=99, full_name="Bot", is_bot=True),
            text="I'm a bot",
            date=datetime.now(timezone.utc),
            message_thread_id=None,
            message_id=2,
            entities=None,
        )

        with patch.object(adapter, "normalize") as mock_norm:
            await adapter._on_message(bot_msg)

        mock_norm.assert_not_called()
        inbound_bus.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_voice_message_forwarded_with_public_trust(
        self, tmp_path: Path
    ) -> None:
        """Voice messages reach the pipeline with trust_level=PUBLIC.

        The voice path now routes via _pipeline.run() (not push_to_hub_guarded()).
        We assert that normalize_audio is called with PUBLIC trust and that the
        pipeline receives the message.
        """
        from unittest.mock import patch

        from lyra.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(
            bot_id="main",
            token="tok",
            inbound_bus=MagicMock(),
        )
        adapter.bot = AsyncMock()

        voice_msg = SimpleNamespace(
            chat=SimpleNamespace(id=123, type="private"),
            from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
            text=None,
            date=datetime.now(timezone.utc),
            message_thread_id=None,
            message_id=3,
            voice=SimpleNamespace(file_id="f123", duration=5),
            audio=None,
            video_note=None,
            entities=None,
            reply_to_message=None,
        )

        audio_file = tmp_path / "voice.ogg"
        audio_file.write_bytes(b"audio")

        _fake_dl = AsyncMock(return_value=(audio_file, 5.0))
        mock_pipeline = MagicMock()
        mock_pipeline.run = AsyncMock(return_value=None)
        with (
            patch(
                "lyra.adapters.telegram.telegram_inbound._download_audio", new=_fake_dl
            ),
            patch(
                "lyra.adapters.telegram.telegram_inbound.normalize_audio"
            ) as mock_norm_audio,
            patch(
                "lyra.adapters.telegram.telegram_inbound._pipeline",
                mock_pipeline,
            ),
        ):
            _stub_msg = MagicMock()
            _stub_msg.pending_attachment = None
            mock_norm_audio.return_value = _stub_msg
            await adapter._on_voice_message(voice_msg)

        # normalize_audio called with PUBLIC trust
        mock_norm_audio.assert_called_once()
        call_kwargs = mock_norm_audio.call_args
        assert call_kwargs.kwargs.get("trust_level") == TrustLevel.PUBLIC
        # Voice now routes via the pipeline, not push_to_hub_guarded
        mock_pipeline.run.assert_awaited_once()
