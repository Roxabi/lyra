from __future__ import annotations

import logging
import os
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from lyra.transport.typing_event import TypingEvent
from lyra.typing.types import (
    CoroFactory,
    FactoryBuilder,
    ScopeResolver,
    TypingManagerProtocol,
)

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS
    from nats.aio.msg import Msg
    from nats.aio.subscription import Subscription

log = logging.getLogger(__name__)


def make_typing_factory(
    worker_fn: Callable[[int], Coroutine[Any, Any, None]],
) -> FactoryBuilder:
    """Stage-axis builder for typing FactoryBuilder closures (#1396, ADR-073).

    Collapses per-adapter `_build_<platform>_typing_factory` methods into a
    shared helper. `worker_fn` is a `scope_id → Coroutine` callable; this wraps
    it in the `(scope_id) → () → Coroutine` shape that TypingTaskManager expects.
    """

    def factory_builder(scope_id: int) -> CoroFactory:
        def _factory() -> Coroutine[Any, Any, None]:
            return worker_fn(scope_id)

        return _factory

    return factory_builder


class TypingListener:
    def __init__(  # noqa: PLR0913
        self,
        nc: "NATS",
        subject: str,
        resolver: ScopeResolver,
        factory_builder: FactoryBuilder,
        manager: TypingManagerProtocol,
        *,
        enabled: bool | None = None,
    ) -> None:
        self._nc = nc
        self._subject = subject
        self._resolver = resolver
        self._factory_builder = factory_builder
        self._manager = manager
        self._enabled = (
            enabled
            if enabled is not None
            else os.getenv("LYRA_TYPING_ENABLED", "false").lower() == "true"
        )
        self._sub: "Subscription | None" = None

    async def start(self) -> None:
        if not self._enabled:
            log.debug(
                "typing_listener: disabled, subscription skipped subject=%s",
                self._subject,
            )
            return  # AC4 flag-off no-op
        self._sub = await self._nc.subscribe(self._subject, cb=self._on_msg)

    async def stop(self) -> None:
        if self._sub is not None:
            await self._sub.unsubscribe()
            self._sub = None

    async def _on_msg(self, msg: "Msg") -> None:
        if not self._enabled:
            # AC4 defense-in-depth; start() gates subscribe, guards race/test-driver
            return
        target: int | None = None
        try:
            event = TypingEvent.model_validate_json(msg.data)
            target = self._resolver(event.scope)
            if event.kind == "started":
                self._manager.start(target, self._factory_builder(target))
            else:
                self._manager.cancel(target)
        except Exception:
            log.exception("typing_listener: dispatch failed subject=%s", self._subject)
            if target is not None:
                try:
                    self._manager.cancel(target)
                except Exception:
                    log.exception("typing_listener: defensive cancel failed")
