"""Tests for llm_overlay bootstrap helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from lyra.bootstrap.llm_overlay import init_nats_llm
from lyra.llm.drivers.nats_driver import NatsLlmDriver


class TestInitNatsLlm:
    async def test_none_nc_returns_none(self) -> None:
        # Arrange / Act
        result = await init_nats_llm(None)
        # Assert
        assert result is None

    async def test_nats_url_unset_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.delenv("NATS_URL", raising=False)
        nc = AsyncMock()
        # Act
        result = await init_nats_llm(nc)
        # Assert
        assert result is None

    async def test_nats_url_set_returns_started_driver(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
        nc = AsyncMock()
        sub_mock = AsyncMock()
        nc.subscribe = AsyncMock(return_value=sub_mock)

        # Act
        driver = await init_nats_llm(nc)

        # Assert — driver returned and start() was called (hb_sub set)
        assert isinstance(driver, NatsLlmDriver)
        assert driver._hb_sub is not None
        nc.subscribe.assert_awaited_once()
