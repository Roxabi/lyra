"""Tests for _bootstrap_unified orchestration (#1451 T6).

Mock all boundary collaborators at the unified module boundary.
No real NATS, no real Hub lifecycle.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import nats.errors
import pytest

from lyra.bootstrap.types import LifecycleResources
from tests.factories.bootstrap import _patch_nats_stubs


@pytest.fixture
def _patch_unified_boundaries(  # noqa: PLR0915
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Patch every boundary collaborator inside lyra.bootstrap.factory.unified."""
    import lyra.bootstrap.factory.unified as unified_mod

    # Already patched by _patch_nats_stubs: ensure_nats, acquire_lockfile,
    # release_lockfile, nc, embedded.  We re-patch release_lockfile below
    # with a spy so we can assert on it.
    _patch_nats_stubs(monkeypatch)

    # ------------------------------------------------------------------
    # Track call order via a shared list
    # ------------------------------------------------------------------
    order: list[str] = []

    def _track(name: str, fn: Any) -> Any:
        """Wrap fn so it appends `name` to order before executing."""
        if inspect.iscoroutinefunction(fn):
            async def _async_wrapper(*a: Any, **kw: Any) -> Any:
                order.append(name)
                return await fn(*a, **kw)
            return _async_wrapper
        def _sync_wrapper(*a: Any, **kw: Any) -> Any:
            order.append(name)
            return fn(*a, **kw)
        return _sync_wrapper

    # -- ensure_nats & lockfile (replace the no-op stubs with tracking versions)
    fake_nc = AsyncMock()
    fake_nc.close = AsyncMock()
    fake_embedded = MagicMock()
    fake_embedded.stop = AsyncMock()

    _orig_ensure_nats = AsyncMock(return_value=(fake_nc, fake_embedded, "nats://fake:4222"))
    monkeypatch.setattr(
        unified_mod,
        "ensure_nats",
        _track("ensure_nats", _orig_ensure_nats),
    )

    _orig_acquire = MagicMock()
    monkeypatch.setattr(
        unified_mod,
        "acquire_lockfile",
        _track("acquire_lockfile", _orig_acquire),
    )

    _orig_release = MagicMock()
    monkeypatch.setattr(
        unified_mod,
        "release_lockfile",
        _track("release_lockfile", _orig_release),
    )

    # -- open_stores async context manager
    fake_stores = MagicMock()
    fake_stores.auth = MagicMock()
    fake_stores.agent = MagicMock()
    fake_stores.message_index = MagicMock()
    fake_stores.prefs = MagicMock()
    fake_stores.turn = MagicMock()
    fake_stores.identity_alias = MagicMock()

    class _FakeStoresCtx:
        async def __aenter__(self) -> MagicMock:
            order.append("open_stores.enter")
            return fake_stores
        async def __aexit__(self, *_exc: Any) -> None:
            order.append("open_stores.exit")

    monkeypatch.setattr(
        unified_mod,
        "open_stores",
        lambda _vault_dir: _FakeStoresCtx(),
    )

    # -- helper mocks with realistic return values
    fake_inbound_bus = MagicMock()
    monkeypatch.setattr(
        unified_mod,
        "_init_inbound_bus",
        _track("_init_inbound_bus", AsyncMock(return_value=fake_inbound_bus)),
    )

    monkeypatch.setattr(
        unified_mod,
        "_prune_message_index",
        _track("_prune_message_index", AsyncMock()),
    )
    monkeypatch.setattr(
        unified_mod,
        "_seed_auth",
        _track("_seed_auth", AsyncMock()),
    )

    fake_bundle = MagicMock()
    fake_bundle.admin_user_ids = ["admin-1"]
    monkeypatch.setattr(
        unified_mod,
        "_init_bot_auths_and_agents",
        _track("_init_bot_auths_and_agents", AsyncMock(return_value=fake_bundle)),
    )

    fake_pm = MagicMock()
    monkeypatch.setattr(
        unified_mod,
        "_init_pairing",
        _track("_init_pairing", AsyncMock(return_value=fake_pm)),
    )

    fake_voice = MagicMock()
    fake_voice.nats_llm_client = MagicMock()
    fake_voice.nats_llm_client.stop = AsyncMock()
    monkeypatch.setattr(
        unified_mod,
        "_init_voice_services",
        _track("_init_voice_services", AsyncMock(return_value=fake_voice)),
    )

    fake_hub = MagicMock()
    monkeypatch.setattr(
        unified_mod,
        "_build_hub",
        _track("_build_hub", lambda *_a, **_kw: fake_hub),
    )

    fake_clipool = MagicMock()
    fake_clipool.cli_nats_driver = MagicMock()
    fake_clipool.cli_nats_driver.stop = AsyncMock()
    fake_clipool.cli_pool = MagicMock()
    fake_clipool.cli_pool.drain_audit_tasks = AsyncMock()
    fake_clipool.worker = MagicMock()
    monkeypatch.setattr(
        unified_mod,
        "_init_clipool",
        _track("_init_clipool", AsyncMock(return_value=fake_clipool)),
    )

    monkeypatch.setattr(
        unified_mod,
        "_register_agents",
        _track("_register_agents", lambda *_a, **_kw: None),
    )

    fake_wired = MagicMock()
    monkeypatch.setattr(
        unified_mod,
        "_wire_adapters",
        _track("_wire_adapters", AsyncMock(return_value=fake_wired)),
    )

    fake_task = MagicMock()
    fake_task.cancel = MagicMock()
    monkeypatch.setattr(
        unified_mod,
        "_run_clipool_worker_task",
        _track("_run_clipool_worker_task", AsyncMock(return_value=fake_task)),
    )

    # run_lifecycle: default is return immediately; tests may override
    _orig_run_lifecycle = AsyncMock()
    monkeypatch.setattr(
        unified_mod,
        "run_lifecycle",
        _track("run_lifecycle", _orig_run_lifecycle),
    )

    # asyncio.gather at module boundary
    _orig_gather = AsyncMock()
    monkeypatch.setattr(unified_mod, "asyncio", MagicMock(gather=_orig_gather))

    return {
        "order": order,
        "fake_nc": fake_nc,
        "fake_embedded": fake_embedded,
        "fake_inbound_bus": fake_inbound_bus,
        "fake_bundle": fake_bundle,
        "fake_pm": fake_pm,
        "fake_voice": fake_voice,
        "fake_hub": fake_hub,
        "fake_clipool": fake_clipool,
        "fake_wired": fake_wired,
        "fake_task": fake_task,
        "fake_stores": fake_stores,
        "run_lifecycle": _orig_run_lifecycle,
        "gather": _orig_gather,
        "acquire_lockfile": _orig_acquire,
        "release_lockfile": _orig_release,
    }


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


async def test_ensure_nats_called_first(
    monkeypatch: pytest.MonkeyPatch,
    _patch_unified_boundaries: dict[str, Any],
) -> None:
    """ensure_nats is awaited before acquire_lockfile and before any helpers."""
    from lyra.bootstrap.factory.unified import _bootstrap_unified

    order = _patch_unified_boundaries["order"]

    raw_config: dict = {}

    await _bootstrap_unified(raw_config)

    assert order[0] == "ensure_nats"
    assert "ensure_nats" in order
    assert order.index("ensure_nats") < order.index("acquire_lockfile")
    assert order.index("ensure_nats") < order.index("_init_inbound_bus")


async def test_sequence_order(
    monkeypatch: pytest.MonkeyPatch,
    _patch_unified_boundaries: dict[str, Any],
) -> None:
    """Helpers called in correct order inside the try block."""
    from lyra.bootstrap.factory.unified import _bootstrap_unified

    order = _patch_unified_boundaries["order"]

    raw_config: dict = {}

    await _bootstrap_unified(raw_config)

    expected_sequence = [
        "ensure_nats",
        "acquire_lockfile",
        "_init_inbound_bus",
        "open_stores.enter",
        "_prune_message_index",
        "_seed_auth",
        "_init_bot_auths_and_agents",
        "_init_pairing",
        "_init_voice_services",
        "_build_hub",
        "_init_clipool",
        "_register_agents",
        "_wire_adapters",
        "_run_clipool_worker_task",
        "run_lifecycle",
        "open_stores.exit",
        "release_lockfile",
    ]

    for name in expected_sequence:
        assert name in order, f"{name!r} was not called"

    # Verify relative ordering of key try-block helpers
    def _before(a: str, b: str) -> None:
        assert order.index(a) < order.index(b), f"expected {a} before {b}"

    _before("_init_inbound_bus", "_prune_message_index")
    _before("_prune_message_index", "_seed_auth")
    _before("_seed_auth", "_init_bot_auths_and_agents")
    _before("_init_bot_auths_and_agents", "_init_pairing")
    _before("_init_pairing", "_init_voice_services")
    _before("_init_voice_services", "_build_hub")
    _before("_build_hub", "_init_clipool")
    _before("_init_clipool", "_register_agents")
    _before("_register_agents", "_wire_adapters")
    _before("_wire_adapters", "_run_clipool_worker_task")
    _before("_run_clipool_worker_task", "run_lifecycle")


async def test_run_lifecycle_awaited_with_resources(
    monkeypatch: pytest.MonkeyPatch,
    _patch_unified_boundaries: dict[str, Any],
) -> None:
    """run_lifecycle awaited with correct LifecycleResources."""
    from lyra.bootstrap.factory.unified import _bootstrap_unified

    fake_pm = _patch_unified_boundaries["fake_pm"]
    fake_nc = _patch_unified_boundaries["fake_nc"]
    fake_hub = _patch_unified_boundaries["fake_hub"]
    fake_wired = _patch_unified_boundaries["fake_wired"]
    run_lifecycle = _patch_unified_boundaries["run_lifecycle"]

    raw_config: dict = {}
    stop_event = asyncio.Event()

    await _bootstrap_unified(raw_config, _stop=stop_event)

    run_lifecycle.assert_awaited_once()
    call_args = run_lifecycle.await_args
    assert call_args is not None
    args, _kwargs = call_args

    # Positional args: hub, wired, resources, _stop
    assert args[0] is fake_hub
    assert args[1] is fake_wired
    resources = args[2]
    assert isinstance(resources, LifecycleResources)
    assert resources.pm is fake_pm
    assert resources.cli_pool is None
    assert resources.nc is fake_nc
    assert args[3] is stop_event


async def test_cleanup_finally(
    monkeypatch: pytest.MonkeyPatch,
    _patch_unified_boundaries: dict[str, Any],
) -> None:
    """On exception inside try, finally still runs all cleanup."""
    from lyra.bootstrap.factory.unified import _bootstrap_unified

    fake_nc = _patch_unified_boundaries["fake_nc"]
    fake_embedded = _patch_unified_boundaries["fake_embedded"]
    fake_voice = _patch_unified_boundaries["fake_voice"]
    fake_clipool = _patch_unified_boundaries["fake_clipool"]
    release_lockfile = _patch_unified_boundaries["release_lockfile"]
    run_lifecycle = _patch_unified_boundaries["run_lifecycle"]

    # Trigger exception inside the try block via run_lifecycle
    run_lifecycle.side_effect = RuntimeError("lifecycle boom")

    raw_config: dict = {}

    with pytest.raises(RuntimeError, match="lifecycle boom"):
        await _bootstrap_unified(raw_config)

    # Voice stop
    fake_voice.nats_llm_client.stop.assert_awaited_once()

    # CliPool stop
    fake_clipool.cli_nats_driver.stop.assert_awaited_once()

    # Audit drain
    fake_clipool.cli_pool.drain_audit_tasks.assert_awaited_once()

    # NATS close
    fake_nc.close.assert_awaited_once()

    # Embedded stop
    fake_embedded.stop.assert_awaited_once()

    # Lockfile release
    release_lockfile.assert_called_once()


async def test_nats_close_error_in_finally_still_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
    _patch_unified_boundaries: dict[str, Any],
) -> None:
    """Even if nc.close() raises nats.errors.Error in finally, cleanup continues."""
    from lyra.bootstrap.factory.unified import _bootstrap_unified

    fake_nc = _patch_unified_boundaries["fake_nc"]
    fake_embedded = _patch_unified_boundaries["fake_embedded"]
    fake_voice = _patch_unified_boundaries["fake_voice"]
    release_lockfile = _patch_unified_boundaries["release_lockfile"]
    run_lifecycle = _patch_unified_boundaries["run_lifecycle"]

    fake_nc.close.side_effect = nats.errors.Error("close failed")
    run_lifecycle.side_effect = RuntimeError("boom")

    raw_config: dict = {}

    with pytest.raises(RuntimeError, match="boom"):
        await _bootstrap_unified(raw_config)

    fake_voice.nats_llm_client.stop.assert_awaited_once()
    fake_embedded.stop.assert_awaited_once()
    release_lockfile.assert_called_once()


async def test_normal_exit_cancels_worker(
    monkeypatch: pytest.MonkeyPatch,
    _patch_unified_boundaries: dict[str, Any],
) -> None:
    """On normal exit, clipool_worker_task.cancel() is called."""
    from lyra.bootstrap.factory.unified import _bootstrap_unified

    fake_task = _patch_unified_boundaries["fake_task"]
    gather = _patch_unified_boundaries["gather"]

    raw_config: dict = {}

    await _bootstrap_unified(raw_config)

    fake_task.cancel.assert_called_once()
    gather.assert_awaited_once()
    # Verify gather called with the task and return_exceptions=True
    g_args, g_kwargs = gather.await_args
    assert g_args[0] is fake_task
    assert g_kwargs.get("return_exceptions") is True
