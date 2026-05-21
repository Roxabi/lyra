"""Tests for bootstrap error on missing /run/secrets/ token file (issue #1057).

Verifies that the adapter bootstrap raises when the expected secret file
bot_token-<bot_id> is absent from LYRA_RUN_SECRETS_DIR, and that the error
message contains the expected path and the install command hint.

Tests are RED until T9 lands the implementation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


async def test_adapter_raises_bootstrap_error_on_missing_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing bot_token-mybot → error raised with path hint and install command."""
    # Arrange — LYRA_RUN_SECRETS_DIR exists but contains no token file for "mybot"
    run_secrets_dir = tmp_path / "run-secrets"
    run_secrets_dir.mkdir()

    monkeypatch.setenv("LYRA_RUN_SECRETS_DIR", str(run_secrets_dir))
    monkeypatch.setenv("NATS_URL", "nats://localhost:4222")

    raw_config = {"telegram": {"bots": [{"bot_id": "mybot"}]}}

    stop = asyncio.Event()
    stop.set()

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
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.wait_for_hub",
            AsyncMock(return_value=True),
        ),
    ):
        # Act + Assert — error raised with path hint and install command
        with pytest.raises(
            Exception,
            match=r"bot_token-mybot",
        ) as exc_info:
            await _bootstrap_adapter_standalone(raw_config, "telegram", _stop=stop)

    # Assert — install command hint is present in the error message
    assert "lyra bot secret install telegram mybot" in str(exc_info.value)
