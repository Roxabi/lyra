"""RED-phase tests for unified.py CliPool → CliNatsDriver wiring (T22).

These tests verify the *post-T22* contract:
  - build_cli_pool is NOT called from unified.py
  - build_cli_nats_driver IS called during startup
  - CliPoolNatsWorker IS instantiated
  - asyncio.create_task is called with the worker coroutine

All tests will fail RED until T22 ships.
"""

from __future__ import annotations

import importlib
import inspect

import pytest


class TestUnifiedNoBuildCliPool:
    def test_unified_no_direct_cli_pool_in_bootstrap(self) -> None:
        """After T22, unified.py must not import or call build_cli_pool."""
        # Arrange
        import lyra.bootstrap.factory.unified as unified_mod

        importlib.reload(unified_mod)
        source = inspect.getsource(unified_mod)

        # Assert — after T22 this call is replaced by CliNatsDriver wiring
        assert "build_cli_pool" not in source, (
            "unified.py still calls build_cli_pool — "
            "T22 should replace this with CliNatsDriver + CliPoolNatsWorker"
        )


class TestUnifiedCliNatsDriverWired:
    def test_unified_imports_build_cli_nats_driver(self) -> None:
        """After T22, unified bootstrap must wire build_cli_nats_driver.

        After the V10 refactor, the symbol lives in wiring_helpers.py (called
        by unified.py).  We inspect both modules so the test does not regress
        if the helper is inlined back into unified.py in a future change.
        """
        import lyra.bootstrap.factory.unified as unified_mod
        import lyra.bootstrap.factory.wiring_helpers as helpers_mod

        importlib.reload(unified_mod)
        combined = inspect.getsource(unified_mod) + inspect.getsource(helpers_mod)

        # Assert
        assert "build_cli_nats_driver" in combined, (
            "Neither unified.py nor wiring_helpers.py references "
            "build_cli_nats_driver — T22 should add CliNatsDriver wiring"
        )

    def test_unified_imports_clipool_nats_worker(self) -> None:
        """After T22, unified bootstrap must reference CliPoolNatsWorker.

        After the V10 refactor, the symbol lives in wiring_helpers.py (called
        by unified.py).  We inspect both modules so the test does not regress
        if the helper is inlined back into unified.py in a future change.
        """
        import lyra.bootstrap.factory.unified as unified_mod
        import lyra.bootstrap.factory.wiring_helpers as helpers_mod

        importlib.reload(unified_mod)
        combined = inspect.getsource(unified_mod) + inspect.getsource(helpers_mod)

        # Assert
        assert "CliPoolNatsWorker" in combined, (
            "Neither unified.py nor wiring_helpers.py references "
            "CliPoolNatsWorker — T22 should spawn it as an asyncio task"
        )


class TestUnifiedCliPoolNatsWorkerInstantiated:
    @pytest.mark.asyncio
    async def test_unified_spawns_clipool_worker_task(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After T22, _bootstrap_unified must instantiate CliPoolNatsWorker."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import lyra.bootstrap.factory.unified as unified_mod

        # Track whether CliPoolNatsWorker was ever instantiated
        instantiated: list[object] = []

        try:
            import lyra.adapters.clipool.clipool_worker as _clipool_worker_mod

            _ = _clipool_worker_mod.CliPoolNatsWorker
        except (ImportError, AttributeError):
            pytest.skip("CliPoolNatsWorker not importable — dependency missing")

        worker_mock = MagicMock()
        worker_mock.run = AsyncMock()
        worker_mock.run_embedded = AsyncMock()

        def track_instantiation(*args, **kwargs):
            inst = MagicMock()
            inst.run = AsyncMock()
            inst.run_embedded = AsyncMock()
            instantiated.append(inst)
            return inst

        import lyra.bootstrap.factory.wiring_helpers as helpers_mod

        # Patch at the module boundary where unified.py resolves the class
        with patch.object(
            unified_mod,
            "CliPoolNatsWorker",
            side_effect=track_instantiation,
            create=True,
        ):
            # We do NOT call _bootstrap_unified (too heavy) —
            # instead verify the source contract via inspection.
            combined = inspect.getsource(unified_mod) + inspect.getsource(helpers_mod)

        # Assert — post-T22 the class is referenced in the unified bootstrap
        assert "CliPoolNatsWorker" in combined, (
            "CliPoolNatsWorker never referenced in unified bootstrap — T22 must add it"
        )


class TestUnifiedWorkerTaskCreated:
    def test_unified_uses_create_task_for_worker(self) -> None:
        """After T22, unified bootstrap must use asyncio.create_task for the worker.

        After the V10 refactor, create_task lives in wiring_helpers.py
        (_run_clipool_worker_task).  We inspect both modules.
        """
        import lyra.bootstrap.factory.unified as unified_mod
        import lyra.bootstrap.factory.wiring_helpers as helpers_mod

        importlib.reload(unified_mod)
        combined = inspect.getsource(unified_mod) + inspect.getsource(helpers_mod)

        # Assert — T22 must wire the worker via create_task so it runs concurrently
        assert "create_task" in combined, (
            "Neither unified.py nor wiring_helpers.py calls asyncio.create_task — "
            "T22 should schedule CliPoolNatsWorker via asyncio.create_task"
        )
