"""Pre/post processor registry for session commands (issue #363).

Replaces the old SessionCommandHandler / _dispatch_session mechanism.
Processors hook into the normal pool flow so responses land in history,
enabling follow-up questions.

Usage — define a new processor::

    from lyra.core.processor_registry import BaseProcessor, register

    @register("/my-cmd", description="Do something cool: /my-cmd <url>")
    class MyCmdProcessor(BaseProcessor):
        async def pre(self, msg: InboundMessage) -> InboundMessage:
            # Enrich message before pool.submit()
            ...
            return enriched_msg

        async def post(self, msg: InboundMessage, response: Response) -> Response:
            # Side effects after LLM response; may modify the response
            ...
            return response

Adding a new command never requires editing any core file.
"""

from __future__ import annotations

import logging
from abc import ABC
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.core.message import InboundMessage, Response
    from lyra.integrations.base import SessionTools

log = logging.getLogger(__name__)


class BaseProcessor(ABC):
    """Abstract base for pre/post processors.

    One instance is created per request so state set during ``pre()``
    (e.g. the original URL) is safely available in ``post()``.
    """

    def __init__(self, tools: "SessionTools") -> None:
        self.tools = tools

    async def pre(self, msg: "InboundMessage") -> "InboundMessage":
        """Transform message before pool submission.  Default: pass-through."""
        return msg

    async def post(self, msg: "InboundMessage", response: "Response") -> "Response":
        """Side effects after LLM response.  Default: pass-through."""
        return response


@dataclass(frozen=True)
class ProcessorEntry:
    """Registry entry for a processor command."""

    processor_cls: type[BaseProcessor]
    description: str = ""


class ProcessorRegistry:
    """Module-level registry mapping command names → ProcessorEntry.

    Never instantiate directly — use the module-level ``registry`` singleton
    and the ``@register`` decorator.
    """

    def __init__(self) -> None:
        self._entries: dict[str, ProcessorEntry] = {}

    def register(
        self,
        command: str,
        *,
        description: str = "",
    ) -> Callable[[type[BaseProcessor]], type[BaseProcessor]]:
        """Class decorator: register *cls* as the processor for *command*.

        *command* must include the leading slash (e.g. ``"/vault-add"``).
        Raises ``ValueError`` on duplicate registration.
        """

        def decorator(cls: type[BaseProcessor]) -> type[BaseProcessor]:
            if command in self._entries:
                raise ValueError(
                    f"Processor command {command!r} is already registered "
                    f"by {self._entries[command].processor_cls.__name__}. "
                    "Each command may only have one processor."
                )
            self._entries[command] = ProcessorEntry(
                processor_cls=cls,
                description=description,
            )
            log.debug("Registered processor %r → %s", command, cls.__name__)
            return cls

        return decorator

    def get(self, command: str) -> type[BaseProcessor] | None:
        """Return the processor class for *command*, or ``None``."""
        entry = self._entries.get(command)
        return entry.processor_cls if entry is not None else None

    def commands(self) -> set[str]:
        """Return all registered command names."""
        return set(self._entries)

    def descriptions(self) -> dict[str, str]:
        """Return {command: description} for /help display."""
        return {cmd: e.description for cmd, e in self._entries.items()}

    def build(self, command: str, tools: "SessionTools") -> BaseProcessor | None:
        """Instantiate a fresh processor for *command* with the given tools.

        Returns ``None`` if the command has no registered processor.
        """
        cls = self.get(command)
        if cls is None:
            return None
        return cls(tools)

    def clear(self) -> None:
        """Remove all registered processors.  Use only in tests — not thread-safe."""
        self._entries.clear()


# Module-level singleton — import and use this everywhere.
registry = ProcessorRegistry()

# Convenience re-export so callers can write:
#   from lyra.core.processor_registry import register
register = registry.register
