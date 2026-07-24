"""Tests for OmpRpcDriver SessionAware protocol (T6).

Falsification: run `git stash -- src/factory/llm/drivers/omp_rpc.py` (before
T6 changes) then `pytest tests/llm/drivers/test_omp_rpc_session.py -x`.

Expected result:
  - test_queue_resume_resolves_and_stashes   → FAIL (AttributeError: no queue_resume)
  - test_complete_injects_provider_session_id → FAIL (provider_session_id absent)
  - test_complete_persists_session_file       → FAIL (no queue_resume / no persist)
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.llm.drivers.omp_rpc import OmpRpcDriver
from roxabi_contracts.jobs import JobResult
from roxabi_contracts.jobs.fixtures import sample_job_result_ok

pytestmark = pytest.mark.omp_contract

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _encode_result(result: JobResult) -> bytes:
    return result.model_dump_json().encode()


def _make_store(
    *,
    cli_session: str | None = "cli-tok-abc",
) -> AsyncMock:
    """Return a fake _OmpSessionStore."""
    store = AsyncMock()
    store.get_cli_session = AsyncMock(return_value=cli_session)
    store._set_cli_session = AsyncMock()  # noqa: SLF001
    return store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def nc() -> AsyncMock:
    mock_nc = AsyncMock()
    sub = AsyncMock()
    mock_nc.subscribe.return_value = sub
    return mock_nc


@pytest.fixture()
def sub(nc: AsyncMock) -> AsyncMock:
    return nc.subscribe.return_value


@pytest.fixture()
def model_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.model_dump.return_value = {"backend": "omp-rpc", "model": "grok-4-fast"}
    return cfg


@pytest.fixture()
def driver(nc: AsyncMock) -> OmpRpcDriver:
    return OmpRpcDriver(nc, timeout_s=5.0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOmpRpcDriverSession:
    # -- (1) queue_resume resolves and stashes --

    @pytest.mark.asyncio
    async def test_queue_resume_resolves_and_stashes(
        self, driver: OmpRpcDriver
    ) -> None:
        """queue_resume() looks up cli_session_id from the store and stashes it.

        Contract:
          - returns True when cli_session_id is found
          - _pending_resume[pool_id] is set to the resolved token
        """
        store = _make_store(cli_session="cli-tok-abc")
        driver.set_turn_store(store)

        result = await driver.queue_resume(pool_id="pool-1", session_id="lyra-sess-1")

        assert result is True
        store.get_cli_session.assert_awaited_once_with("lyra-sess-1")
        assert driver._pending_resume.get("pool-1") == "cli-tok-abc"  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_queue_resume_returns_false_when_no_session(
        self, driver: OmpRpcDriver
    ) -> None:
        """queue_resume() returns False and leaves _pending_resume empty when
        no cli_session_id exists for the given session_id."""
        store = _make_store(cli_session=None)
        driver.set_turn_store(store)

        result = await driver.queue_resume(pool_id="pool-x", session_id="no-such")

        assert result is False
        assert "pool-x" not in driver._pending_resume  # noqa: SLF001

    # -- (2) complete() injects provider_session_id --

    @pytest.mark.asyncio
    async def test_complete_injects_provider_session_id(
        self,
        driver: OmpRpcDriver,
        nc: AsyncMock,
        sub: AsyncMock,
        model_cfg: MagicMock,
    ) -> None:
        """After queue_resume(), complete() injects provider_session_id into payload.

        Contract:
          - published envelope payload contains provider_session_id == resolved token
          - _pending_resume is cleared after the call (token consumed exactly once)
        """
        store = _make_store(cli_session="cli-tok-xyz")
        driver.set_turn_store(store)
        await driver.queue_resume(pool_id="pool-1", session_id="lyra-sess-1")

        success_result = JobResult.model_validate(
            {**sample_job_result_ok, "data": {"result": "ok"}}
        )
        sub.next_msg.return_value = SimpleNamespace(data=_encode_result(success_result))

        res = await driver.complete(
            pool_id="pool-1",
            text="hello",
            model_cfg=model_cfg,
            system_prompt="sys",
        )

        assert res.ok is True

        published_bytes = nc.publish.await_args.args[1]
        envelope = json.loads(published_bytes)
        payload = envelope["payload"]
        assert payload.get("provider_session_id") == "cli-tok-xyz"
        # B1 regression-lock: pool_id MUST ride the wire — the worker routes on it.
        assert payload.get("pool_id") == "pool-1"

        # Token consumed — second call must NOT re-inject
        sub.next_msg.return_value = SimpleNamespace(data=_encode_result(success_result))
        await driver.complete(
            pool_id="pool-1", text="again", model_cfg=model_cfg, system_prompt="sys"
        )
        second_payload = json.loads(nc.publish.await_args.args[1])["payload"]
        assert "provider_session_id" not in second_payload

    @pytest.mark.asyncio
    async def test_complete_no_injection_without_queue_resume(
        self,
        driver: OmpRpcDriver,
        sub: AsyncMock,
        nc: AsyncMock,
        model_cfg: MagicMock,
    ) -> None:
        """complete() must NOT inject provider_session_id.

        Precondition: queue_resume was NOT called before this complete().
        """
        success_result = JobResult.model_validate(
            {**sample_job_result_ok, "data": {"result": "ok"}}
        )
        sub.next_msg.return_value = SimpleNamespace(data=_encode_result(success_result))

        await driver.complete(
            pool_id="pool-2", text="hi", model_cfg=model_cfg, system_prompt="s"
        )

        published_bytes = nc.publish.await_args.args[1]
        payload = json.loads(published_bytes)["payload"]
        assert "provider_session_id" not in payload

    # -- (3) complete() persists session_file --

    @pytest.mark.asyncio
    async def test_complete_persists_session_file(
        self,
        driver: OmpRpcDriver,
        sub: AsyncMock,
        model_cfg: MagicMock,
    ) -> None:
        """On success with session_file, TurnPublisher.publish_set_cli_session is used.

        Contract (ADR-075): hub must not SQLite-write turns.db — publish only.
        """
        store = _make_store()
        publisher = AsyncMock()
        driver.set_turn_store(store)
        driver.set_turn_publisher(publisher)
        driver.link_lyra_session("pool-3", "lyra-sess-99")

        success_result = JobResult.model_validate(
            {
                **sample_job_result_ok,
                "data": {
                    "result": "done",
                    "session_file": "/tmp/omp/.omp/sessions/sess-abc.json",
                },
            }
        )
        sub.next_msg.return_value = SimpleNamespace(data=_encode_result(success_result))

        res = await driver.complete(
            pool_id="pool-3", text="q", model_cfg=model_cfg, system_prompt="s"
        )

        assert res.ok is True
        publisher.publish_set_cli_session.assert_awaited_once()
        kw = publisher.publish_set_cli_session.await_args.kwargs
        assert kw["session_id"] == "lyra-sess-99"
        assert kw["cli_session_id"] == "/tmp/omp/.omp/sessions/sess-abc.json"
        store._set_cli_session.assert_not_called()  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_complete_skips_persist_when_no_session_file(
        self,
        driver: OmpRpcDriver,
        sub: AsyncMock,
        model_cfg: MagicMock,
    ) -> None:
        """When JobResult.data has no session_file, publisher is NOT called."""
        store = _make_store()
        publisher = AsyncMock()
        driver.set_turn_store(store)
        driver.set_turn_publisher(publisher)
        driver.link_lyra_session("pool-4", "lyra-sess-42")

        success_result = JobResult.model_validate(
            {**sample_job_result_ok, "data": {"result": "done"}}
        )
        sub.next_msg.return_value = SimpleNamespace(data=_encode_result(success_result))

        await driver.complete(
            pool_id="pool-4", text="q", model_cfg=model_cfg, system_prompt="s"
        )

        publisher.publish_set_cli_session.assert_not_awaited()
        store._set_cli_session.assert_not_awaited()  # noqa: SLF001

    # -- (4) cross-conversation isolation --

    @pytest.mark.asyncio
    async def test_cross_conversation_isolation(
        self,
        driver: OmpRpcDriver,
        nc: AsyncMock,
        sub: AsyncMock,
        model_cfg: MagicMock,
    ) -> None:
        """Tokens from distinct sessions never bleed across pools.

        Contract:
          - Each pool_id stashes the token belonging to its own session_id
          - complete() for poolA injects tokA (not tokB), and vice-versa
          - _pending_resume is consumed independently per pool
        """
        # Arrange — store returns a different token per session_id
        session_tokens: dict[str, str] = {"sessA": "tokA", "sessB": "tokB"}

        store = AsyncMock()
        store.get_cli_session = AsyncMock(
            side_effect=lambda sid: session_tokens.get(sid)
        )
        store._set_cli_session = AsyncMock()  # noqa: SLF001
        driver.set_turn_store(store)

        driver.link_lyra_session("poolA", "sessA")
        driver.link_lyra_session("poolB", "sessB")

        await driver.queue_resume(pool_id="poolA", session_id="sessA")
        await driver.queue_resume(pool_id="poolB", session_id="sessB")

        # Assert stash contains correct tokens for both pools, no bleed
        assert driver._pending_resume == {"poolA": "tokA", "poolB": "tokB"}  # noqa: SLF001

        success_result = JobResult.model_validate(
            {**sample_job_result_ok, "data": {"result": "ok"}}
        )

        # Act + Assert — poolA injects tokA
        sub.next_msg.return_value = SimpleNamespace(data=_encode_result(success_result))
        await driver.complete(
            pool_id="poolA", text="hello", model_cfg=model_cfg, system_prompt="sys"
        )
        published_bytes_a = nc.publish.await_args.args[1]
        payload_a = json.loads(published_bytes_a)["payload"]
        assert payload_a.get("provider_session_id") == "tokA"
        assert "poolA" not in driver._pending_resume  # noqa: SLF001

        # Act + Assert — poolB injects tokB (not tokA)
        sub.next_msg.return_value = SimpleNamespace(data=_encode_result(success_result))
        await driver.complete(
            pool_id="poolB", text="world", model_cfg=model_cfg, system_prompt="sys"
        )
        published_bytes_b = nc.publish.await_args.args[1]
        payload_b = json.loads(published_bytes_b)["payload"]
        assert payload_b.get("provider_session_id") == "tokB"
        assert "poolB" not in driver._pending_resume  # noqa: SLF001
