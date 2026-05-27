"""Tests for bootstrap factory bot_agent_map — resolve_bot_agent_map."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.bootstrap.factory.bot_agent_map import resolve_bot_agent_map

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bot_cfg(bot_id: str, agent: str | None = None) -> Any:
    """Return a plain object shaped like TelegramBotConfig / DiscordBotConfig.

    Using a real ``SimpleNamespace`` instead of ``MagicMock`` prevents
    ``getattr(bot_cfg, 'agent', None)`` from auto-creating a truthy child mock.
    """
    import types

    m = types.SimpleNamespace()
    m.bot_id = bot_id
    if agent is not None:
        m.agent = agent
    return m


def _make_store(
    *,
    bot_agent_map: dict[tuple[str, str], str | None] | None = None,
    agent_rows: dict[str, MagicMock | None] | None = None,
    set_bot_agent_side_effect: Exception | None = None,
) -> MagicMock:
    """Build a fake AgentStore with the specified behaviours."""
    store = MagicMock()

    _ba_map = bot_agent_map or {}
    store.get_bot_agent = MagicMock(
        side_effect=lambda platform, bot_id: _ba_map.get((platform, bot_id))
    )

    _rows = agent_rows or {}
    store.get = MagicMock(side_effect=lambda name: _rows.get(name))

    if set_bot_agent_side_effect is not None:
        store.set_bot_agent = AsyncMock(side_effect=set_bot_agent_side_effect)
    else:
        store.set_bot_agent = AsyncMock(return_value=None)

    return store


# ---------------------------------------------------------------------------
# DB cache hit with valid agent → included
# ---------------------------------------------------------------------------


async def test_db_cache_hit_valid_agent() -> None:
    """Existing DB row pointing to a live agent is included in the map."""
    store = _make_store(
        bot_agent_map={("telegram", "main"): "lyra_default"},
        agent_rows={"lyra_default": MagicMock(name="lyra_default")},
    )
    tg_bot = _make_bot_cfg("main")

    result = await resolve_bot_agent_map(store, [tg_bot], [])

    assert result == {("telegram", "main"): "lyra_default"}
    store.set_bot_agent.assert_not_awaited()


# ---------------------------------------------------------------------------
# DB cache hit with stale agent (deleted) → logged + skipped
# ---------------------------------------------------------------------------


async def test_db_cache_hit_stale_agent_logs_and_skips(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DB row references an agent that no longer exists → error logged, bot skipped."""
    store = _make_store(
        bot_agent_map={("discord", "main"): "deleted_agent"},
        agent_rows={"deleted_agent": None},
    )
    dc_bot = _make_bot_cfg("main")

    with caplog.at_level(logging.ERROR):
        result = await resolve_bot_agent_map(store, [], [dc_bot])

    assert result == {}
    assert "not found in agents table" in caplog.text
    assert "deleted_agent" in caplog.text


# ---------------------------------------------------------------------------
# No DB cache + TOML agent valid → seeded to DB + included
# ---------------------------------------------------------------------------


async def test_toml_agent_valid_seeds_and_includes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No DB row but valid TOML agent → seeds mapping, logs info, includes bot."""
    store = _make_store(
        bot_agent_map={},
        agent_rows={"lyra_default": MagicMock(name="lyra_default")},
    )
    tg_bot = _make_bot_cfg("main", agent="lyra_default")

    with caplog.at_level(logging.INFO):
        result = await resolve_bot_agent_map(store, [tg_bot], [])

    assert result == {("telegram", "main"): "lyra_default"}
    store.set_bot_agent.assert_awaited_once_with("telegram", "main", "lyra_default")
    assert "seeding from TOML" in caplog.text


# ---------------------------------------------------------------------------
# No DB cache + TOML agent missing → logged + skipped
# ---------------------------------------------------------------------------


async def test_toml_agent_missing_logs_and_skips(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No DB row and TOML agent not in agents DB → error logged, bot skipped."""
    store = _make_store(
        bot_agent_map={},
        agent_rows={"missing_agent": None},
    )
    dc_bot = _make_bot_cfg("main", agent="missing_agent")

    with caplog.at_level(logging.ERROR):
        result = await resolve_bot_agent_map(store, [], [dc_bot])

    assert result == {}
    assert "not found in agents DB" in caplog.text
    assert "missing_agent" in caplog.text


# ---------------------------------------------------------------------------
# No DB cache + no TOML agent → logged + skipped
# ---------------------------------------------------------------------------


async def test_no_db_no_toml_agent_logs_and_skips(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Neither DB row nor TOML agent → error logged, bot skipped."""
    store = _make_store(bot_agent_map={}, agent_rows={})
    tg_bot = _make_bot_cfg("orphan")

    with caplog.at_level(logging.ERROR):
        result = await resolve_bot_agent_map(store, [tg_bot], [])

    assert result == {}
    assert "no DB row and no TOML agent" in caplog.text
    assert "orphan" in caplog.text


# ---------------------------------------------------------------------------
# set_bot_agent failure → logged + still included (resilient)
# ---------------------------------------------------------------------------


async def test_set_bot_agent_failure_logs_warning_still_includes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DB seed failure does not abort wiring; bot is still included."""
    exc = RuntimeError("disk full")
    store = _make_store(
        bot_agent_map={},
        agent_rows={"lyra_default": MagicMock(name="lyra_default")},
        set_bot_agent_side_effect=exc,
    )
    dc_bot = _make_bot_cfg("main", agent="lyra_default")

    with caplog.at_level(logging.WARNING):
        result = await resolve_bot_agent_map(store, [], [dc_bot])

    assert result == {("discord", "main"): "lyra_default"}
    assert "failed to seed" in caplog.text
    assert "disk full" in caplog.text


# ---------------------------------------------------------------------------
# Empty bot lists → empty result
# ---------------------------------------------------------------------------


async def test_empty_bot_lists_returns_empty() -> None:
    """Passing no bots at all yields an empty mapping."""
    store = _make_store()

    result = await resolve_bot_agent_map(store, [], [])

    assert result == {}
    store.get_bot_agent.assert_not_called()


# ---------------------------------------------------------------------------
# Mixed platform batch
# ---------------------------------------------------------------------------


async def test_mixed_batch_partial(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Multiple bots across platforms with mixed outcomes."""
    store = _make_store(
        bot_agent_map={("telegram", "good"): "agent_a"},
        agent_rows={
            "agent_a": MagicMock(name="agent_a"),
            "agent_b": MagicMock(name="agent_b"),
        },
    )
    tg_good = _make_bot_cfg("good")
    tg_orphan = _make_bot_cfg("orphan")
    dc_seed = _make_bot_cfg("seed_me", agent="agent_b")

    with caplog.at_level(logging.INFO):
        result = await resolve_bot_agent_map(store, [tg_good, tg_orphan], [dc_seed])

    assert result == {
        ("telegram", "good"): "agent_a",
        ("discord", "seed_me"): "agent_b",
    }
    store.set_bot_agent.assert_awaited_once_with("discord", "seed_me", "agent_b")
    assert "orphan" not in result
