"""roxabi-obs reporter tests."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from roxabi_obs.reporter import FleetReporter, _read_build_revision

from roxabi_contracts.fleet import CONTAINER_REPORT


@pytest.mark.asyncio
async def test_publish_payload_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTAINER_NAME", "factory-hub")
    monkeypatch.setenv("IMAGE_REF", "ghcr.io/roxabi/factory:staging-svc")
    monkeypatch.setenv("IMAGE_REVISION", "deadbeef")
    nc = AsyncMock()
    reporter = FleetReporter(nc, interval_s=0.01)
    await reporter._publish_once()
    nc.publish.assert_awaited_once()
    subject, payload = nc.publish.await_args.args
    assert subject == CONTAINER_REPORT
    data = json.loads(payload.decode())
    assert data["container_name"] == "factory-hub"
    assert data["image_revision"] == "deadbeef"


@pytest.mark.asyncio
async def test_missing_container_name_exits_without_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONTAINER_NAME", raising=False)
    nc = AsyncMock()
    reporter = FleetReporter(nc, container_name="")
    await reporter.run()
    nc.publish.assert_not_called()


def test_read_build_revision_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_REVISION", "abc")
    assert _read_build_revision() == "abc"


def test_read_build_revision_from_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("IMAGE_REVISION", raising=False)
    path = tmp_path / "build.json"
    path.write_text(json.dumps({"revision": "from-file"}), encoding="utf-8")
    monkeypatch.setattr("roxabi_obs.reporter._BUILD_INFO_PATH", path)
    assert _read_build_revision() == "from-file"