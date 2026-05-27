"""Tests for llm_overlay bootstrap helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from lyra.bootstrap.factory.llm_overlay import init_nats_llm
from lyra.llm.llm_client import LlmClient
from roxabi_contracts.llm import SUBJECTS


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

        # Assert — driver returned and pool.start() was called (subscribe invoked)
        assert isinstance(driver, LlmClient)
        nc.subscribe.assert_awaited_once()

    async def test_request_subject_is_generate_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: init_nats_llm must route to the generic LLM subject."""
        # Arrange
        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
        nc = AsyncMock()
        nc.subscribe = AsyncMock(return_value=AsyncMock())

        # Act
        driver = await init_nats_llm(nc)

        # Assert
        assert isinstance(driver, LlmClient)
        assert driver._request_subject == SUBJECTS.generate_request
