"""Shared pytest fixtures for the Lyra test suite."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.hub import Hub

# Backward-compatible re-exports from bootstrap factories
from tests.factories.bootstrap import (  # noqa: F401
    _FakeDcAdapter,
    _FakeDp,
    _FakeTgAdapter,
    _patch_nats_stubs,
    _reset_version_check_log_state,
    make_fake_stores,
    patch_all,
    patch_auth_config_test,
    patch_bootstrap_common,
)

__all__ = [
    "_FakeDcAdapter",
    "_FakeDp",
    "_FakeTgAdapter",
    "_patch_nats_stubs",
    "_reset_version_check_log_state",
    "make_fake_stores",
    "patch_all",
    "patch_auth_config_test",
    "patch_bootstrap_common",
]

# ---------------------------------------------------------------------------
# Health endpoint shared constants
# ---------------------------------------------------------------------------

HEALTH_SECRET = "test-health-secret"

# Timeout constants for event-based coordination
TIMEOUT_FAST = 0.5  # In-memory operations
TIMEOUT_IO = 2.0  # Single network round-trip
TIMEOUT_SLOW = 5.0  # Multi-step coordination, CI variance buffer


async def yield_once() -> None:
    """Yield control to the event loop once. Replaces asyncio.sleep(0)."""
    await asyncio.sleep(0)


async def _drain(pool: Any, *, timeout: float = TIMEOUT_IO) -> None:
    """Yield to the event loop then wait for the current task to finish."""
    await yield_once()
    if pool._current_task is not None:
        await asyncio.wait_for(pool._current_task, timeout=timeout)


AUTH_HEADERS = {"authorization": f"Bearer {HEALTH_SECRET}"}


# ---------------------------------------------------------------------------
# Health endpoint shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def circuit_registry() -> CircuitRegistry:
    registry = CircuitRegistry()
    for name in ("claude-cli", "telegram", "discord", "hub"):
        registry.register(CircuitBreaker(name=name))
    return registry


@pytest.fixture()
def hub(circuit_registry: CircuitRegistry) -> Hub:
    return Hub(circuit_registry=circuit_registry)


def pytest_configure(config: pytest.Config) -> None:
    """Pre-flight: ensure .venv is synced before collecting tests.

    In fresh worktrees the venv may exist but be incomplete (e.g. concurrent
    ``uv run`` created it without finishing the install).  Running ``uv sync``
    once at session start is a fast no-op when everything is current and
    prevents flaky hangs from competing installs.
    """
    venv = Path(".venv")
    if not venv.exists() or not (venv / "bin" / "python").exists():
        subprocess.run(
            [sys.executable, "-m", "uv", "sync"],
            check=True,
            capture_output=True,
        )


# ---------------------------------------------------------------------------
# Agent store fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def patch_agent_store(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch AgentStore in main_mod with a MagicMock. Returns the fake store."""
    import lyra.__main__ as main_mod

    _fake_agent_store = MagicMock()
    _fake_agent_store.connect = AsyncMock()
    _fake_agent_store.close = AsyncMock()
    _fake_agent_store.get_bot_agent = MagicMock(return_value=None)
    _fake_agent_store.get = MagicMock(return_value=None)
    _fake_agent_store.set_bot_agent = AsyncMock()
    monkeypatch.setattr(main_mod, "AgentStore", lambda **kwargs: _fake_agent_store)
    return _fake_agent_store
