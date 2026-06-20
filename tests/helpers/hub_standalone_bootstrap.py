"""Shared stubs for ``_bootstrap_hub_standalone`` behavioral tests.

Hub bootstrap gains new pre-announce lifecycle hooks over time. Patching them
inside a parenthesized ``with (patch(), ...)`` tuple hits CPython's 20
statically-nested-block limit (one nested ``with`` per context manager).

All hub bootstrap stubs go through :func:`stub_hub_bootstrap` (monkeypatch only)
so behavioral tests stay scalable. See ``6c50e8eb`` / #1946.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

_HUB = "factory.bootstrap.standalone.hub_standalone"
_ACTIVE_JOBS_KV = "factory.infrastructure.stores.jobs.active_jobs_kv"
_ACTIVE_JOBS_REFRESH = "factory.infrastructure.stores.jobs.active_jobs_refresher"
_STREAM_SETUP = "factory.infrastructure.outbound_audio.stream_setup"

HookFactory = Callable[[], Awaitable[Any] | Any]


def make_hub_stubs() -> tuple[MagicMock, Callable[..., Any]]:
    """Return ``(mock_nc, fake_open_stores)`` for hub bootstrap short-circuit tests."""
    mock_nc = AsyncMock()
    mock_nc.is_connected = True
    mock_nc.close = AsyncMock()
    mock_nc.drain = AsyncMock()
    mock_js = MagicMock()
    mock_nc.jetstream = MagicMock(return_value=mock_js)

    @asynccontextmanager
    async def _fake_open_stores(*_args: object, **_kwargs: object):
        stores = MagicMock()
        stores.message_index.cleanup_older_than = AsyncMock(return_value=0)
        stores.auth = MagicMock()
        stores.bot = MagicMock()
        stores.identity_alias = MagicMock()
        stores.agent = MagicMock()
        yield stores

    return mock_nc, _fake_open_stores


def hub_test_config() -> dict[str, object]:
    return {
        "defaults": {"cwd": "/tmp"},
        "admin": {"user_ids": ["test_admin"]},
        "telegram": {"bots": []},
        "discord": {"bots": []},
        "auth": {"telegram_bots": [], "discord_bots": []},
        "message_index": {},
    }


def _hub_wire_return() -> tuple[
    MagicMock, list[object], list[object], MagicMock, MagicMock
]:
    return (
        MagicMock(inbound_bus=AsyncMock(start=AsyncMock())),
        [],
        [],
        MagicMock(),
        MagicMock(),
    )


def _recording_hook(
    call_order: list[str],
    name: str,
    *,
    return_value: object = None,
) -> HookFactory:
    async def _hook(*_args: object, **_kwargs: object) -> object:
        call_order.append(name)
        return return_value

    return _hook


@dataclass
class HubBootstrapStubOptions:
    """Optional overrides for :func:`stub_hub_bootstrap`."""

    call_order: list[str] | None = None
    ensure_stream: HookFactory | BaseException | None = None
    ensure_kv: HookFactory | None = None
    ensure_active_jobs_kv: HookFactory | None = None
    publish_watch_channels: HookFactory | None = None
    publish_bot_roster: HookFactory | None = None
    announce_hub_ready: HookFactory | MagicMock | None = None
    _resolved: dict[str, HookFactory | MagicMock] = field(
        default_factory=dict, repr=False
    )

    def resolve(self) -> dict[str, HookFactory | MagicMock]:
        if self._resolved:
            return self._resolved
        order = self.call_order
        if order is not None:
            if self.ensure_stream is None:
                self.ensure_stream = _recording_hook(order, "ensure_stream")
            if self.ensure_kv is None:
                self.ensure_kv = _recording_hook(
                    order, "ensure_kv", return_value=MagicMock()
                )
            if self.ensure_active_jobs_kv is None:
                self.ensure_active_jobs_kv = _recording_hook(
                    order, "ensure_active_jobs_kv", return_value=MagicMock()
                )
            if self.publish_watch_channels is None:
                self.publish_watch_channels = _recording_hook(
                    order, "publish_watch_channels"
                )
            if self.publish_bot_roster is None:
                self.publish_bot_roster = _recording_hook(order, "publish_bot_roster")

        if isinstance(self.ensure_stream, BaseException):
            ensure_stream_impl: HookFactory | MagicMock = AsyncMock(
                side_effect=self.ensure_stream
            )
        elif self.ensure_stream is None:
            ensure_stream_impl = AsyncMock()
        else:
            ensure_stream_impl = self.ensure_stream

        self._resolved = {
            "ensure_stream": ensure_stream_impl,
            "ensure_kv": (
                self.ensure_kv
                if self.ensure_kv is not None
                else AsyncMock(return_value=MagicMock())
            ),
            "ensure_active_jobs_kv": (
                self.ensure_active_jobs_kv
                if self.ensure_active_jobs_kv is not None
                else AsyncMock(return_value=MagicMock())
            ),
            "publish_watch_channels": (
                self.publish_watch_channels
                if self.publish_watch_channels is not None
                else AsyncMock()
            ),
            "publish_bot_roster": (
                self.publish_bot_roster
                if self.publish_bot_roster is not None
                else AsyncMock()
            ),
            "announce_hub_ready": (
                self.announce_hub_ready
                if self.announce_hub_ready is not None
                else AsyncMock()
            ),
        }
        return self._resolved


def _apply_pre_announce_stubs(
    monkeypatch: pytest.MonkeyPatch,
    hooks: dict[str, HookFactory | MagicMock],
) -> None:
    monkeypatch.setattr(
        f"{_ACTIVE_JOBS_KV}.ensure_active_jobs_kv",
        hooks["ensure_active_jobs_kv"],
    )
    monkeypatch.setattr(
        f"{_ACTIVE_JOBS_KV}.KvActiveJobsStore",
        MagicMock(return_value=MagicMock(connect=AsyncMock())),
    )
    monkeypatch.setattr(
        f"{_ACTIVE_JOBS_REFRESH}.RegistryCoordinator",
        MagicMock(return_value=MagicMock(start=MagicMock(), stop=AsyncMock())),
    )
    monkeypatch.setattr(f"{_STREAM_SETUP}.ensure_stream", hooks["ensure_stream"])
    monkeypatch.setattr(f"{_STREAM_SETUP}.ensure_kv", hooks["ensure_kv"])
    monkeypatch.setattr(
        f"{_HUB}.publish_watch_channels", hooks["publish_watch_channels"]
    )
    monkeypatch.setattr(f"{_HUB}.publish_bot_roster", hooks["publish_bot_roster"])
    monkeypatch.setattr(f"{_HUB}.announce_hub_ready", hooks["announce_hub_ready"])


def _apply_core_hub_stubs(
    monkeypatch: pytest.MonkeyPatch,
    mock_nc: MagicMock,
    fake_open_stores: Callable[..., Any],
) -> None:
    monkeypatch.setattr(f"{_HUB}.acquire_lockfile", lambda: None)
    monkeypatch.setattr(f"{_HUB}.release_lockfile", lambda: None)
    monkeypatch.setattr(f"{_HUB}.nats_connect", AsyncMock(return_value=mock_nc))
    monkeypatch.setattr(f"{_HUB}.open_stores", fake_open_stores)
    monkeypatch.setattr(f"{_HUB}.seed_grants_from_bots", AsyncMock())
    monkeypatch.setattr(
        f"{_HUB}.build_bot_auths",
        lambda *_a, **_kw: (MagicMock(), [], [], []),
    )
    monkeypatch.setattr(
        f"{_HUB}._resolve_bot_agent_map",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        f"{_HUB}.load_agent_configs",
        lambda *_a, **_kw: {"default": MagicMock()},
    )
    monkeypatch.setattr(
        "factory.bootstrap.factory.config._load_messages",
        lambda *_a, **_kw: MagicMock(),
    )
    monkeypatch.setattr(
        f"{_HUB}.build_pairing_manager",
        AsyncMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        f"{_HUB}._build_hub_and_wire",
        AsyncMock(return_value=_hub_wire_return()),
    )
    monkeypatch.setattr(
        f"{_HUB}.start_mint_failure_subscriber",
        AsyncMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(f"{_HUB}.log_contracts_version", lambda: None)
    monkeypatch.setattr(
        f"{_HUB}.build_inbound_bus",
        lambda *_a, **_kw: (AsyncMock(), MagicMock()),
    )


def stub_hub_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    mock_nc: MagicMock,
    fake_open_stores: Callable[..., Any],
    options: HubBootstrapStubOptions | None = None,
    **kwargs: object,
) -> None:
    """Monkeypatch every ``_bootstrap_hub_standalone`` dependency."""
    opts = options or HubBootstrapStubOptions(**kwargs)  # type: ignore[arg-type]
    _apply_pre_announce_stubs(monkeypatch, opts.resolve())
    _apply_core_hub_stubs(monkeypatch, mock_nc, fake_open_stores)
