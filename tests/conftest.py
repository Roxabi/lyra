"""Shared pytest fixtures for the Lyra test suite."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import lyra.__main__ as main_mod


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


@pytest.fixture
def patch_agent_store(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch AgentStore in main_mod with a MagicMock. Returns the fake store."""
    _fake_agent_store = MagicMock()
    _fake_agent_store.connect = AsyncMock()
    _fake_agent_store.close = AsyncMock()
    _fake_agent_store.get_bot_agent = MagicMock(return_value=None)
    _fake_agent_store.get = MagicMock(return_value=None)
    _fake_agent_store.set_bot_agent = AsyncMock()
    monkeypatch.setattr(main_mod, "AgentStore", lambda **kwargs: _fake_agent_store)
    return _fake_agent_store
