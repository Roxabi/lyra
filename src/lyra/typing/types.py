from collections.abc import Callable, Coroutine
from typing import Any, Protocol, runtime_checkable

from lyra.transport.work_scope import WorkScope

CoroFactory = Callable[[], Coroutine[Any, Any, None]]
FactoryBuilder = Callable[[int], CoroFactory]


@runtime_checkable
class ScopeResolver(Protocol):
    """Per-platform: WorkScope → platform-native typing target int.

    Implementations are module-level functions in src/lyra/adapters/{platform}/
    so they are import-time testable without instantiating the adapter (AC8).
    """

    def __call__(self, scope: WorkScope, /) -> int: ...


@runtime_checkable
class TypingManagerProtocol(Protocol):
    """Structural Protocol satisfied by lyra.adapters.shared.TypingTaskManager.

    Defined here in stage-axis module so TypingListener depends ONLY on this
    Protocol — never imports lyra.adapters directly (would invert layer stack).
    """

    def start(
        self,
        target: int,
        coro_factory: CoroFactory,
    ) -> None: ...

    def cancel(self, target: int) -> None: ...
