"""V1 slice gate — merge-blocking green-light for #1813 runtime-selection V1.

All 3 tests must PASS before V1 can merge:
  1. Hub has factory.job.*.result subscribe grant in ACL matrix.
  2. "omp-rpc" is a recognised backend in agent_config._VALID_BACKENDS.
  3. OmpRpcDriver round-trip mock: complete() returns ok=True + result="pong".
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WORKTREE_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Test 1 — ACL matrix: hub has factory.job.*.result subscribe grant
# ---------------------------------------------------------------------------


def test_hub_has_result_subscribe_grant() -> None:
    """Hub identity must subscribe to factory.job.*.result (NATS result envelope)."""
    acl_path = _WORKTREE_ROOT / "deploy" / "nats" / "acl-matrix.json"
    d: dict = json.loads(acl_path.read_text())
    hub_subscribe: list[str] = d["identities"]["hub"]["subscribe"]
    assert "factory.job.*.result" in hub_subscribe


# ---------------------------------------------------------------------------
# Test 2 — agent_config: "omp-rpc" is a valid backend
# ---------------------------------------------------------------------------


def test_omp_rpc_in_agent_config_valid_backends() -> None:
    """_VALID_BACKENDS must include 'omp-rpc' so agent seeds can select it."""
    from factory.core.agent.agent_config import _VALID_BACKENDS  # noqa: PLC0415

    assert "omp-rpc" in _VALID_BACKENDS


# ---------------------------------------------------------------------------
# Test 3 — OmpRpcDriver round-trip mock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_omp_driver_round_trip_mock() -> None:
    """OmpRpcDriver.complete() returns ok=True + result='pong' on a mock NATS reply."""
    from factory.llm.drivers.omp_rpc import OmpRpcDriver  # noqa: PLC0415
    from roxabi_contracts.jobs import JobResult  # noqa: PLC0415
    from roxabi_contracts.jobs.fixtures import sample_job_result_ok  # noqa: PLC0415

    # Arrange — mock NATS connection + subscription
    nc = AsyncMock()
    sub = AsyncMock()
    nc.subscribe.return_value = sub

    result_payload = JobResult.model_validate(
        {**sample_job_result_ok, "data": {"result": "pong"}}
    )
    sub.next_msg.return_value = SimpleNamespace(
        data=result_payload.model_dump_json().encode()
    )

    model_cfg = MagicMock()
    model_cfg.model_dump.return_value = {"backend": "omp-rpc", "model": "grok-4-fast"}

    driver = OmpRpcDriver(nc, timeout_s=5.0)

    # Act
    res = await driver.complete(
        pool_id="p",
        text="ping",
        model_cfg=model_cfg,
        system_prompt="",
    )

    # Assert
    assert res.ok is True
    assert res.result == "pong"
