"""Shared pytest fixtures for the factory test suite."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Generator
from contextvars import Token
from pathlib import Path
from typing import Any

import pytest

from factory.core.hub import Hub
from factory.core.lifecycle.circuit_breaker import CircuitBreaker, CircuitRegistry
from factory.core.trace import TraceContext

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
    "_LOAD_BOT_TOKEN_PATH",
    "_patch_nats_stubs",
    "_reset_version_check_log_state",
    "make_fake_stores",
    "patch_all",
    "patch_auth_config_test",
    "patch_bootstrap_common",
]

# ---------------------------------------------------------------------------
# Patch-path constants — centralised so a future relocation of load_bot_token
# is a one-line change here rather than a grep-across-13-files exercise.
# ---------------------------------------------------------------------------

_LOAD_BOT_TOKEN_PATH = "factory.bootstrap.credentials.load_bot_token"

# ---------------------------------------------------------------------------
# Health endpoint shared constants
# ---------------------------------------------------------------------------

HEALTH_SECRET = "test-health-secret"
_DEFAULT_TEST_TRACE_ID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def _default_trace_context(
    request: pytest.FixtureRequest,
) -> Generator[Token[str] | None, None, None]:
    """Hub work-path codecs require TraceContext on encode (#2069)."""
    if request.node.get_closest_marker("no_default_trace"):
        yield None
        return
    token = TraceContext.set_trace_id(_DEFAULT_TEST_TRACE_ID)
    yield token
    try:
        TraceContext.reset_trace_id(token)
    except RuntimeError:
        # A test may reset the autouse token to assert missing-trace behavior.
        pass

# Timeout constants for event-based coordination
TIMEOUT_FAST = 0.5  # In-memory operations
TIMEOUT_IO = 2.0  # Single network round-trip
TIMEOUT_SLOW = 5.0  # Multi-step coordination, CI variance buffer


async def yield_once() -> None:
    """Yield control to the event loop once. Replaces asyncio.sleep(0)."""
    await asyncio.sleep(0)  # event-based


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
# Audio consumer no-op (T8 bootstrap wiring) — tests/ root scope
# ---------------------------------------------------------------------------

# Patch targets: must match where start_audio_consumer is imported in the
# wiring modules.  If these paths move, tests that rely on this no-op will
# start seeing real NATS calls and fail immediately — making drift visible.
_AUDIO_CONSUMER_PATCH_TARGETS = (
    # After #1663: start_audio_consumer lives in _standalone_wiring_common,
    # shared by both standalone_telegram and standalone_discord.
    "factory.bootstrap.wiring._standalone_wiring_common.start_audio_consumer",
)

# Narrow allowlist: only these test-file name fragments trigger the no-op.
# Kept as a secondary gate so that files outside tests/bootstrap/ that
# accidentally call _bootstrap_adapter_standalone are patched rather than
# silently hitting real NATS.
_NOOP_AUDIO_CONSUMER_FILES = frozenset(
    [
        "test_bootstrap_credential_resolution",
    ]
)


@pytest.fixture(autouse=True)
def _noop_audio_consumer_root(request: pytest.FixtureRequest) -> object:
    """Patch start_audio_consumer to a no-op for tests that call bootstrap but
    don't exercise audio consumer behaviour (T8).

    Activation rules (either condition is sufficient):
      - Test is marked with ``@pytest.mark.audio_consumer_live`` → skip patch
        (test exercises the real consumer or applies its own inner patch).
      - Test file name matches a fragment in _NOOP_AUDIO_CONSUMER_FILES → apply
        the no-op patch.
      - Otherwise → do not patch (most of the suite is unaffected).

    Tests in tests/bootstrap/ are handled by their own conftest.
    """
    if request.node.get_closest_marker("audio_consumer_live") is not None:
        yield
        return

    node_id = request.node.nodeid
    if not any(f in node_id for f in _NOOP_AUDIO_CONSUMER_FILES):
        yield
        return

    from unittest.mock import AsyncMock, patch

    noop = AsyncMock(return_value=AsyncMock())
    with patch(_AUDIO_CONSUMER_PATCH_TARGETS[0], noop):
        yield


