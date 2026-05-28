"""Tests for bootstrap credential resolution — /run/secrets/ read path (issue #1057).

Verifies that the adapter reads bot tokens from the filesystem path
os.environ.get("LYRA_RUN_SECRETS_DIR", "/run/secrets") via the
lyra.bootstrap.credentials.load_bot_token helper, rather than from
CredentialStore (which was deleted in #1057).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


async def test_adapter_reads_token_from_run_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TelegramAdapter receives the token read from LYRA_RUN_SECRETS_DIR."""
    # Arrange
    run_secrets_dir = tmp_path / "run-secrets"
    run_secrets_dir.mkdir()
    (run_secrets_dir / "bot_token-mybot").write_text("TKN_VAL")

    monkeypatch.setenv("LYRA_RUN_SECRETS_DIR", str(run_secrets_dir))
    monkeypatch.setenv("NATS_URL", "nats://localhost:4222")

    raw_config = {"telegram": {"bots": [{"bot_id": "mybot"}]}}

    stop = asyncio.Event()
    stop.set()

    captured_kwargs: list[dict] = []

    mock_adapter = AsyncMock()
    mock_adapter._bot_id = "mybot"
    mock_adapter.resolve_identity = AsyncMock()
    mock_adapter.astart = AsyncMock()
    mock_adapter.close = AsyncMock()
    mock_adapter.dp.start_polling = AsyncMock(return_value=None)
    mock_adapter.dp.stop_polling = AsyncMock(return_value=None)

    def _capture_tg(**kwargs: object) -> object:
        captured_kwargs.append(dict(kwargs))
        return mock_adapter

    mock_nc = AsyncMock()
    mock_inbound_bus = AsyncMock()
    mock_inbound_bus.register = MagicMock()
    mock_inbound_bus.start = AsyncMock()
    mock_inbound_bus.stop = AsyncMock()

    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", return_value=mock_inbound_bus),
        patch("lyra.adapters.telegram.TelegramAdapter", side_effect=_capture_tg),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.DeadLetterConsumer",
            return_value=AsyncMock(),
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        patch("lyra.bootstrap.credentials._is_prod_env", return_value=False),
    ):
        # Act
        await _bootstrap_adapter_standalone(raw_config, "telegram", _stop=stop)

    # Assert — TelegramAdapter constructed with the token from /run/secrets/
    assert len(captured_kwargs) == 1
    assert captured_kwargs[0]["token"] == "TKN_VAL"


async def test_adapter_reads_webhook_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TelegramAdapter receives both token and webhook_secret when both files exist."""
    # Arrange
    run_secrets_dir = tmp_path / "run-secrets"
    run_secrets_dir.mkdir()
    (run_secrets_dir / "bot_token-mybot").write_text("TKN_VAL")
    (run_secrets_dir / "bot_webhook-mybot").write_text("WHK_VAL")

    monkeypatch.setenv("LYRA_RUN_SECRETS_DIR", str(run_secrets_dir))
    monkeypatch.setenv("NATS_URL", "nats://localhost:4222")

    raw_config = {"telegram": {"bots": [{"bot_id": "mybot"}]}}

    stop = asyncio.Event()
    stop.set()

    captured_kwargs: list[dict] = []

    mock_adapter = AsyncMock()
    mock_adapter._bot_id = "mybot"
    mock_adapter.resolve_identity = AsyncMock()
    mock_adapter.astart = AsyncMock()
    mock_adapter.close = AsyncMock()
    mock_adapter.dp.start_polling = AsyncMock(return_value=None)
    mock_adapter.dp.stop_polling = AsyncMock(return_value=None)

    def _capture_tg(**kwargs: object) -> object:
        captured_kwargs.append(dict(kwargs))
        return mock_adapter

    mock_nc = AsyncMock()
    mock_inbound_bus = AsyncMock()
    mock_inbound_bus.register = MagicMock()
    mock_inbound_bus.start = AsyncMock()
    mock_inbound_bus.stop = AsyncMock()

    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", return_value=mock_inbound_bus),
        patch("lyra.adapters.telegram.TelegramAdapter", side_effect=_capture_tg),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.DeadLetterConsumer",
            return_value=AsyncMock(),
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        patch("lyra.bootstrap.credentials._is_prod_env", return_value=False),
    ):
        # Act
        await _bootstrap_adapter_standalone(raw_config, "telegram", _stop=stop)

    # Assert — both token and webhook_secret present
    assert len(captured_kwargs) == 1
    assert captured_kwargs[0]["token"] == "TKN_VAL"
    assert captured_kwargs[0].get("webhook_secret") == "WHK_VAL"


async def test_adapter_omits_webhook_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TelegramAdapter receives token but webhook_secret is None when file absent."""
    # Arrange — only token file, no webhook file
    run_secrets_dir = tmp_path / "run-secrets"
    run_secrets_dir.mkdir()
    (run_secrets_dir / "bot_token-mybot").write_text("TKN_VAL")

    monkeypatch.setenv("LYRA_RUN_SECRETS_DIR", str(run_secrets_dir))
    monkeypatch.setenv("NATS_URL", "nats://localhost:4222")

    raw_config = {"telegram": {"bots": [{"bot_id": "mybot"}]}}

    stop = asyncio.Event()
    stop.set()

    captured_kwargs: list[dict] = []

    mock_adapter = AsyncMock()
    mock_adapter._bot_id = "mybot"
    mock_adapter.resolve_identity = AsyncMock()
    mock_adapter.astart = AsyncMock()
    mock_adapter.close = AsyncMock()
    mock_adapter.dp.start_polling = AsyncMock(return_value=None)
    mock_adapter.dp.stop_polling = AsyncMock(return_value=None)

    def _capture_tg(**kwargs: object) -> object:
        captured_kwargs.append(dict(kwargs))
        return mock_adapter

    mock_nc = AsyncMock()
    mock_inbound_bus = AsyncMock()
    mock_inbound_bus.register = MagicMock()
    mock_inbound_bus.start = AsyncMock()
    mock_inbound_bus.stop = AsyncMock()

    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", return_value=mock_inbound_bus),
        patch("lyra.adapters.telegram.TelegramAdapter", side_effect=_capture_tg),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.DeadLetterConsumer",
            return_value=AsyncMock(),
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        patch("lyra.bootstrap.credentials._is_prod_env", return_value=False),
    ):
        # Act
        await _bootstrap_adapter_standalone(raw_config, "telegram", _stop=stop)

    # Assert — token present, webhook_secret absent (None or empty string)
    assert len(captured_kwargs) == 1
    assert captured_kwargs[0]["token"] == "TKN_VAL"
    webhook = captured_kwargs[0].get("webhook_secret")
    assert webhook is None or webhook == ""


async def test_adapter_handles_multi_bot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two bots configured — each TelegramAdapter constructed with its own token."""
    # Arrange
    run_secrets_dir = tmp_path / "run-secrets"
    run_secrets_dir.mkdir()
    (run_secrets_dir / "bot_token-bot1").write_text("TOKEN_BOT1")
    (run_secrets_dir / "bot_token-bot2").write_text("TOKEN_BOT2")

    monkeypatch.setenv("LYRA_RUN_SECRETS_DIR", str(run_secrets_dir))
    monkeypatch.setenv("NATS_URL", "nats://localhost:4222")

    raw_config = {"telegram": {"bots": [{"bot_id": "bot1"}, {"bot_id": "bot2"}]}}

    stop = asyncio.Event()
    stop.set()

    captured_kwargs: list[dict] = []
    call_count = 0
    bots = ["bot1", "bot2"]

    def _make_mock_adapter(bot_id: str) -> AsyncMock:
        mock = AsyncMock()
        mock._bot_id = bot_id
        mock.resolve_identity = AsyncMock()
        mock.astart = AsyncMock()
        mock.close = AsyncMock()
        mock.dp.start_polling = AsyncMock(return_value=None)
        mock.dp.stop_polling = AsyncMock(return_value=None)
        return mock

    def _capture_tg(**kwargs: object) -> object:
        nonlocal call_count
        bot_id = bots[call_count] if call_count < len(bots) else "unknown"
        call_count += 1
        captured_kwargs.append(dict(kwargs))
        return _make_mock_adapter(str(kwargs.get("bot_id", bot_id)))

    mock_nc = AsyncMock()
    mock_inbound_bus = AsyncMock()
    mock_inbound_bus.register = MagicMock()
    mock_inbound_bus.start = AsyncMock()
    mock_inbound_bus.stop = AsyncMock()

    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", return_value=mock_inbound_bus),
        patch("lyra.adapters.telegram.TelegramAdapter", side_effect=_capture_tg),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.DeadLetterConsumer",
            return_value=AsyncMock(),
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        patch("lyra.bootstrap.credentials._is_prod_env", return_value=False),
    ):
        # Act
        await _bootstrap_adapter_standalone(raw_config, "telegram", _stop=stop)

    # Assert — two adapters each with their own token
    assert len(captured_kwargs) == 2
    tokens = {kw["token"] for kw in captured_kwargs}
    assert "TOKEN_BOT1" in tokens
    assert "TOKEN_BOT2" in tokens


async def test_discord_adapter_handles_multi_bot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two Discord bots — each adapter starts with its own token (CR-6)."""
    # Arrange
    run_secrets_dir = tmp_path / "run-secrets"
    run_secrets_dir.mkdir()
    (run_secrets_dir / "bot_token-bot1").write_text("DC_TOKEN_BOT1")
    (run_secrets_dir / "bot_token-bot2").write_text("DC_TOKEN_BOT2")

    monkeypatch.setenv("LYRA_RUN_SECRETS_DIR", str(run_secrets_dir))
    monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
    monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path / "vault"))

    raw_config = {
        "discord": {
            "bots": [
                {"bot_id": "bot1", "auto_thread": True, "thread_hot_hours": 24},
                {"bot_id": "bot2", "auto_thread": True, "thread_hot_hours": 24},
            ]
        }
    }

    stop = asyncio.Event()
    stop.set()

    captured_start_tokens: list[str] = []

    def _make_mock_adapter(bot_id: str) -> AsyncMock:
        mock = AsyncMock()
        mock._bot_id = bot_id
        mock.astart = AsyncMock()
        mock.close = AsyncMock()

        async def _start(token: str) -> None:
            captured_start_tokens.append(token)
            # Yield once so cancellation can land in the same loop tick.
            await asyncio.sleep(0)

        mock.start = AsyncMock(side_effect=_start)
        return mock

    call_count = 0

    def _capture_dc(**kwargs: object) -> object:
        nonlocal call_count
        bot_id = str(kwargs.get("bot_id", f"bot{call_count + 1}"))
        call_count += 1
        return _make_mock_adapter(bot_id)

    mock_nc = AsyncMock()
    mock_inbound_bus = AsyncMock()
    mock_inbound_bus.register = MagicMock()
    mock_inbound_bus.start = AsyncMock()
    mock_inbound_bus.stop = AsyncMock()

    # Patch stores so they don't touch real SQLite during bootstrap.
    mock_agent_store = AsyncMock()
    mock_agent_store.get_bot_settings = MagicMock(return_value={})

    mock_thread_store = AsyncMock()
    mock_turn_store = AsyncMock()

    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", return_value=mock_inbound_bus),
        patch("lyra.adapters.discord.DiscordAdapter", side_effect=_capture_dc),
        patch(
            "lyra.infrastructure.stores.agent_store.AgentStore",
            return_value=mock_agent_store,
        ),
        patch(
            "lyra.infrastructure.stores.thread_store.ThreadStore",
            return_value=mock_thread_store,
        ),
        patch(
            "lyra.infrastructure.stores.turn_store.TurnStore",
            return_value=mock_turn_store,
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.DeadLetterConsumer",
            return_value=AsyncMock(),
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        patch("lyra.bootstrap.credentials._is_prod_env", return_value=False),
    ):
        # Act
        await _bootstrap_adapter_standalone(raw_config, "discord", _stop=stop)

    # Assert — both bots received their dedicated token via .start(token)
    assert "DC_TOKEN_BOT1" in captured_start_tokens, (
        f"Expected DC_TOKEN_BOT1 in {captured_start_tokens}"
    )
    assert "DC_TOKEN_BOT2" in captured_start_tokens, (
        f"Expected DC_TOKEN_BOT2 in {captured_start_tokens}"
    )
