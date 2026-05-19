"""Shared Protocol types for CliPool mixins."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..agent.agent_config import ModelConfig
    from .cli_pool_entry import _ProcessEntry


class _CliPoolCore(Protocol):  # pyright: ignore[reportUnusedClass] — DEBT:protocol-private-ducktyping
    """Protocol declaring cross-mixin dependencies shared by CliPool mixins."""

    async def _idle_reaper(self) -> None: ...

    async def _kill(self, pool_id: str, *, preserve_session: bool = True) -> None: ...

    async def _spawn(  # noqa: PLR0913
        self,
        pool_id: str,
        model_config: "ModelConfig",
        system_prompt: str = "",
        agent_name: str | None = None,
        agent_email: str | None = None,
        lyra_session_id: str | None = None,
    ) -> "_ProcessEntry | None": ...

    async def reset(self, pool_id: str) -> None: ...


# TYPE_CHECKING-only imports: runtime callers must not call get_type_hints() on
# _CliPoolCore or any class that Protocol-checks against it.
