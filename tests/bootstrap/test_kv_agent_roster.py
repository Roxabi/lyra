"""Unit tests for factory.bootstrap.wiring.kv_agent_roster."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from nats.js.errors import BucketNotFoundError, KeyNotFoundError

from factory.bootstrap.wiring.kv_agent_roster import seed_web_agent_roster
from factory.core.agent.agent_models import AgentRow
from factory.infrastructure.kv.agent_roster import publish_agent_roster
from roxabi_contracts.state.agent_roster import WEB_ROSTER_KEY, WebAgentRosterDocument


def _mock_kv(
    *, get_entry: object | None = None, puts: dict[str, bytes] | None = None
) -> MagicMock:
    kv = MagicMock()
    store = puts if puts is not None else {}

    async def _put(key: str, value: bytes) -> None:
        store[key] = value

    async def _get(key: str) -> MagicMock:
        if get_entry is None:
            raise KeyNotFoundError
        if isinstance(get_entry, Exception):
            raise get_entry
        entry = MagicMock()
        entry.value = get_entry
        return entry

    kv.put = AsyncMock(side_effect=_put)
    kv.get = AsyncMock(side_effect=_get)
    kv._store = store
    return kv


def _mock_js(*, kv: MagicMock | None = None) -> MagicMock:
    js = MagicMock()
    js.key_value = AsyncMock(return_value=kv or _mock_kv())
    js.create_key_value = AsyncMock(return_value=kv or _mock_kv())
    return js


def _make_agent_row(name: str = "lyra_default") -> AgentRow:
    return AgentRow(name=name, backend="claude", model="sonnet")


@pytest.mark.asyncio
async def test_publish_agent_roster_writes_roster_web() -> None:
    kv = _mock_kv()
    js = _mock_js(kv=kv)
    agent_store = MagicMock()
    agent_store.get_all.return_value = [
        _make_agent_row("helper"),
        _make_agent_row("lyra_default"),
    ]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "factory.infrastructure.kv.agent_roster.open_or_create_kv",
            AsyncMock(return_value=kv),
        )
        await publish_agent_roster(js, agent_store)

    assert WEB_ROSTER_KEY in kv._store
    doc = WebAgentRosterDocument.model_validate_json(kv._store[WEB_ROSTER_KEY])
    assert doc.agents == ["helper", "lyra_default"]
    assert "backend" not in json.loads(kv._store[WEB_ROSTER_KEY])


@pytest.mark.asyncio
async def test_publish_agent_roster_skips_invalid_agent_name() -> None:
    kv = _mock_kv()
    js = _mock_js(kv=kv)
    agent_store = MagicMock()
    agent_store.get_all.return_value = [
        _make_agent_row("../../evil"),
        _make_agent_row("lyra_default"),
    ]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "factory.infrastructure.kv.agent_roster.open_or_create_kv",
            AsyncMock(return_value=kv),
        )
        await publish_agent_roster(js, agent_store)

    doc = WebAgentRosterDocument.model_validate_json(kv._store[WEB_ROSTER_KEY])
    assert doc.agents == ["lyra_default"]


@pytest.mark.asyncio
async def test_seed_web_agent_roster_returns_names() -> None:
    doc = WebAgentRosterDocument(
        updated_at="2026-06-19T12:00:00Z",
        agents=["lyra_default", "helper"],
    )
    kv = _mock_kv(get_entry=doc.model_dump_json().encode())
    js = _mock_js(kv=kv)

    result = await seed_web_agent_roster(js)

    assert result == ["lyra_default", "helper"]


@pytest.mark.asyncio
async def test_seed_web_agent_roster_missing_key_exits() -> None:
    kv = _mock_kv(get_entry=KeyNotFoundError())
    js = _mock_js(kv=kv)

    with pytest.raises(SystemExit, match="roster missing or invalid"):
        await seed_web_agent_roster(js)


@pytest.mark.asyncio
async def test_seed_web_agent_roster_empty_agents_exits() -> None:
    doc = WebAgentRosterDocument(updated_at="2026-06-19T12:00:00Z", agents=[])
    kv = _mock_kv(get_entry=doc.model_dump_json().encode())
    js = _mock_js(kv=kv)

    with pytest.raises(SystemExit, match="empty agents"):
        await seed_web_agent_roster(js)


@pytest.mark.asyncio
async def test_seed_web_agent_roster_bucket_missing_exits() -> None:
    js = MagicMock()
    js.key_value = AsyncMock(side_effect=BucketNotFoundError)

    with pytest.raises(SystemExit, match="bucket not found"):
        await seed_web_agent_roster(js)


@pytest.mark.asyncio
async def test_seed_web_agent_roster_rejects_config_fields_in_kv() -> None:
    raw = json.dumps(
        {
            "schema_version": 1,
            "updated_at": "2026-06-19T12:00:00Z",
            "agents": ["lyra_default"],
            "model": "sonnet",
        }
    ).encode()
    kv = _mock_kv(get_entry=raw)
    js = _mock_js(kv=kv)

    with pytest.raises(SystemExit, match="roster missing or invalid"):
        await seed_web_agent_roster(js)


@pytest.mark.asyncio
async def test_seed_web_agent_roster_malformed_json_exits() -> None:
    kv = _mock_kv(get_entry=b"not-json{{{")
    js = _mock_js(kv=kv)

    with pytest.raises(SystemExit, match="malformed JSON"):
        await seed_web_agent_roster(js)


@pytest.mark.asyncio
async def test_seed_web_agent_roster_timeout_exits() -> None:
    kv = MagicMock()

    async def _slow_get(_key: str) -> MagicMock:
        await asyncio.sleep(1.0)
        entry = MagicMock()
        entry.value = b"{}"
        return entry

    kv.get = AsyncMock(side_effect=_slow_get)
    js = MagicMock()
    js.key_value = AsyncMock(return_value=kv)

    with pytest.raises(SystemExit, match="timeout"):
        await seed_web_agent_roster(js, timeout=0.05)


@pytest.mark.asyncio
async def test_seed_web_agent_roster_rejects_invalid_agent_name() -> None:
    doc = WebAgentRosterDocument(
        updated_at="2026-06-19T12:00:00Z",
        agents=["../../evil"],
    )
    kv = _mock_kv(get_entry=doc.model_dump_json().encode())
    js = _mock_js(kv=kv)

    with pytest.raises(SystemExit, match="invalid agent name"):
        await seed_web_agent_roster(js)