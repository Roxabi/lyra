"""Unit tests for DlqRouter (no live NATS).

All tests use mock NATS client — no live NATS server required.
Verifies: advisory parsing, MSG.GET → publish → MSG.DELETE flow,
error containment, stop/unsubscribe, and domain extraction.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.infrastructure.jobs.dlq_router import (
    ADVISORY_SUBJECT,
    DlqRouter,
    _extract_domain,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_nc(
    *,
    get_msg_return: MagicMock | None = None,
    get_msg_raises: Exception | None = None,
    delete_msg_return: bool = True,
    delete_msg_raises: Exception | None = None,
    publish_raises: Exception | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Build nc + jsm mocks with configurable side-effects.

    Returns (nc, jsm) so tests can assert on both.
    nc.jsm() is synchronous — use nc.jsm.return_value, NOT AsyncMock.
    """
    raw_msg = MagicMock()
    raw_msg.subject = "factory.jobs.vault.some-task"
    raw_msg.data = b'{"key": "value"}'

    jsm = MagicMock()  # synchronous return value — nc.jsm() is a plain def
    if get_msg_raises is not None:
        jsm.get_msg = AsyncMock(side_effect=get_msg_raises)
    else:
        jsm.get_msg = AsyncMock(return_value=get_msg_return or raw_msg)
    if delete_msg_raises is not None:
        jsm.delete_msg = AsyncMock(side_effect=delete_msg_raises)
    else:
        jsm.delete_msg = AsyncMock(return_value=delete_msg_return)

    nc = MagicMock()
    nc.jsm.return_value = jsm  # nc.jsm() synchronous — NOT AsyncMock
    nc.subscribe = AsyncMock(return_value=MagicMock())
    if publish_raises is not None:
        nc.publish = AsyncMock(side_effect=publish_raises)
    else:
        nc.publish = AsyncMock()

    return nc, jsm


def _advisory_msg(stream_seq: int = 42, deliveries: int = 3) -> MagicMock:
    """Build a mock NATS Msg carrying a MAX_DELIVERIES advisory payload."""
    msg = MagicMock()
    msg.data = json.dumps({"stream_seq": stream_seq, "deliveries": deliveries}).encode()
    return msg


# ---------------------------------------------------------------------------
# test_start_subscribes
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_start_subscribes() -> None:
    """start() must subscribe to ADVISORY_SUBJECT with cb=_handle."""
    nc, _ = _make_nc()
    js = MagicMock()
    router = DlqRouter(nc, js)

    await router.start()

    nc.subscribe.assert_awaited_once()
    call_kwargs = nc.subscribe.call_args
    assert call_kwargs.args[0] == ADVISORY_SUBJECT
    assert call_kwargs.kwargs.get("cb") == router._handle


# ---------------------------------------------------------------------------
# test_stop_unsubscribes
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stop_unsubscribes() -> None:
    """stop() must call sub.unsubscribe() and clear _sub."""
    nc, _ = _make_nc()
    sub_mock = MagicMock()
    sub_mock.unsubscribe = AsyncMock()
    nc.subscribe = AsyncMock(return_value=sub_mock)
    js = MagicMock()
    router = DlqRouter(nc, js)

    await router.start()
    await router.stop()

    sub_mock.unsubscribe.assert_awaited_once()
    assert router._sub is None


# ---------------------------------------------------------------------------
# test_full_flow
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_full_flow() -> None:
    """Happy path: advisory → get_msg → publish DLQ subject → delete_msg."""
    nc, jsm = _make_nc()
    js = MagicMock()
    router = DlqRouter(nc, js)

    msg = _advisory_msg(stream_seq=7, deliveries=5)
    await router._handle(msg)

    # get_msg called with correct stream + seq
    jsm.get_msg.assert_awaited_once_with("FACTORY_JOBS", seq=7)

    # publish called with DLQ subject derived from raw.subject
    nc.publish.assert_awaited_once()
    pub_args = nc.publish.call_args
    assert pub_args.args[0] == "factory.jobs.dlq.vault"
    assert pub_args.args[1] == b'{"key": "value"}'
    headers = pub_args.kwargs.get("headers") or pub_args.args[2]
    assert headers["Roxabi-Dlq-Orig-Subject"] == "factory.jobs.vault.some-task"
    assert headers["Roxabi-Dlq-Deliveries"] == "5"
    assert headers["Roxabi-Dlq-Stream-Seq"] == "7"

    # delete_msg called after successful publish
    jsm.delete_msg.assert_awaited_once_with("FACTORY_JOBS", 7)


# ---------------------------------------------------------------------------
# test_missing_stream_seq_skips
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_missing_stream_seq_skips() -> None:
    """Advisory without stream_seq must skip get_msg entirely."""
    nc, jsm = _make_nc()
    js = MagicMock()
    router = DlqRouter(nc, js)

    msg = MagicMock()
    msg.data = json.dumps({"deliveries": 3}).encode()  # no stream_seq

    await router._handle(msg)

    jsm.get_msg.assert_not_awaited()
    nc.publish.assert_not_awaited()
    jsm.delete_msg.assert_not_awaited()


# ---------------------------------------------------------------------------
# test_get_msg_failure_skips
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_msg_failure_skips() -> None:
    """If get_msg raises, must not publish or delete."""
    nc, jsm = _make_nc(get_msg_raises=RuntimeError("stream error"))
    js = MagicMock()
    router = DlqRouter(nc, js)

    msg = _advisory_msg(stream_seq=99)
    await router._handle(msg)

    jsm.get_msg.assert_awaited_once()
    nc.publish.assert_not_awaited()
    jsm.delete_msg.assert_not_awaited()


# ---------------------------------------------------------------------------
# test_publish_failure_does_not_delete
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_publish_failure_does_not_delete() -> None:
    """Message-loss guard: a failed DLQ publish must NOT delete the original.

    delete_msg may only run AFTER a successful publish — otherwise the job is
    lost (publish failed, yet the source seq was removed from the stream).
    """
    nc, jsm = _make_nc(publish_raises=RuntimeError("publish timeout"))
    js = MagicMock()
    router = DlqRouter(nc, js)

    await router._handle(_advisory_msg(stream_seq=11))

    nc.publish.assert_awaited_once()
    jsm.delete_msg.assert_not_awaited()


# ---------------------------------------------------------------------------
# test_malformed_advisory_json_skips
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_malformed_advisory_json_skips() -> None:
    """A non-JSON advisory payload must be skipped, not crash the handler."""
    nc, jsm = _make_nc()
    js = MagicMock()
    router = DlqRouter(nc, js)

    msg = MagicMock()
    msg.data = b"not-json{"

    await router._handle(msg)  # must not raise

    jsm.get_msg.assert_not_awaited()
    nc.publish.assert_not_awaited()
    jsm.delete_msg.assert_not_awaited()


# ---------------------------------------------------------------------------
# test_dlq_lane_subject_not_rerouted
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dlq_lane_subject_not_rerouted() -> None:
    """A message already on the DLQ lane must not recurse into dlq.dlq.

    If the fetched original is itself a factory.jobs.dlq.* subject, the router
    must skip re-publishing (no factory.jobs.dlq.dlq) and leave it in the stream.
    """
    raw = MagicMock()
    raw.subject = "factory.jobs.dlq.vault"
    raw.data = b"{}"
    nc, jsm = _make_nc(get_msg_return=raw)
    js = MagicMock()
    router = DlqRouter(nc, js)

    await router._handle(_advisory_msg(stream_seq=13))

    jsm.get_msg.assert_awaited_once()
    nc.publish.assert_not_awaited()
    jsm.delete_msg.assert_not_awaited()


# ---------------------------------------------------------------------------
# test_extract_domain_known_lanes
# ---------------------------------------------------------------------------


def test_extract_domain_known_lanes() -> None:
    """Known factory.jobs.<domain>.* subjects must yield correct domain tokens."""
    assert _extract_domain("factory.jobs.vault.task-1") == "vault"
    assert _extract_domain("factory.jobs.web-intel.task-2") == "web-intel"
    assert _extract_domain("factory.jobs.test.task-3") == "test"


# ---------------------------------------------------------------------------
# test_extract_domain_unknown_fallback
# ---------------------------------------------------------------------------


def test_extract_domain_unknown_fallback() -> None:
    """Subjects that do not match factory.jobs.<domain> must return 'unknown'."""
    assert _extract_domain("") == "unknown"
    assert _extract_domain("other.subject") == "unknown"
    assert _extract_domain("factory.other.vault") == "unknown"
    assert _extract_domain("factory.jobs") == "unknown"  # only 2 parts after split
    # DLQ lane must never re-target itself (would yield factory.jobs.dlq.dlq).
    assert _extract_domain("factory.jobs.dlq.vault") == "unknown"


# ---------------------------------------------------------------------------
# test_dlq_router_started_before_announce_hub_ready
# ---------------------------------------------------------------------------


def test_dlq_router_started_before_announce_hub_ready() -> None:
    """AST guard: DlqRouter.start() must precede announce_hub_ready in hub bootstrap.

    Structural assertion ensuring the DLQ router is active before the hub
    signals readiness to the platform (ordering invariant, ADR-088).
    """
    import ast
    import inspect

    import factory.bootstrap.standalone.hub_standalone as _mod

    func_source = inspect.getsource(_mod._bootstrap_hub_standalone)
    tree = ast.parse(func_source)
    positions: dict[str, int] = {
        "start": -1,
        "announce_hub_ready": -1,
    }
    # Match _dlq_router.start() SPECIFICALLY. A bare ".start()" match would bind
    # to the earlier hub.inbound_bus.start() call, making the assertion pass for
    # the wrong receiver (it would hold even if _dlq_router.start() were removed).
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "start"
            and isinstance(func.value, ast.Name)
            and func.value.id == "_dlq_router"
            and positions["start"] == -1
        ):
            positions["start"] = node.lineno
            continue
        name = (
            func.id
            if isinstance(func, ast.Name)
            else (func.attr if isinstance(func, ast.Attribute) else None)
        )
        if name == "announce_hub_ready" and positions["announce_hub_ready"] == -1:
            positions["announce_hub_ready"] = node.lineno

    start_line = positions["start"]
    ready_line = positions["announce_hub_ready"]

    assert start_line != -1, (
        "DlqRouter.start() not found in _bootstrap_hub_standalone"
        " — DLQ router not wired"
    )
    assert ready_line != -1, (
        "announce_hub_ready not found in _bootstrap_hub_standalone — unexpected"
    )
    assert start_line < ready_line, (
        f"DlqRouter.start() (line {start_line}) must precede "
        f"announce_hub_ready (line {ready_line}) — ADR-088 ordering violated"
    )
