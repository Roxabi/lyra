"""EventEmitter[OutT] — SanitizedError → terminal-event translator.

Centralizes the ``SanitizedError → Out`` conversion so that ``str(exc)`` /
``f"...{exc}"`` patterns never appear on streaming bus-bound paths.

Consumed by Phase 5 parser implementations (T15, T16):
  - ``lyra.core.cli.cli_streaming_parser.CliStreamingParser``
  - ``lyra.core.processors.stream_processor.StreamProcessor``

Ordering rules:
  Callers are responsible for flushing pending items BEFORE emitting the
  terminal event.  ``emit_terminal`` does NOT auto-flush.  The canonical
  usage pattern is::

      yield from emitter.flush()
      yield from emitter.emit_terminal(err)

  This preserves the downstream ordering invariant (e.g. ``TextEnd`` before
  ``RunErrorRenderEvent``) that outbound adapters rely on.  See spec #1282
  §Expected Behavior and ``_shared_streaming_state.py`` ``is_error_pending``
  field comment (~lines 150–155).
"""

from __future__ import annotations

from typing import Callable, Generic, Iterable, TypeVar

from lyra.transport import SanitizedError

OutT = TypeVar("OutT")


class EventEmitter(Generic[OutT]):
    """Result-aware terminal-event translator.

    Centralizes ``SanitizedError → Out`` conversion. Consumed by Parser impls
    to keep ``str(exc)`` / ``f"...{exc}"`` out of streaming bus-bound paths.

    ``error_translator`` is the per-consumer adapter that builds the terminal
    Out value (e.g. ``RunErrorRenderEvent(message=err.message)`` or
    ``ResultLlmEvent(is_error=True, worker_error=WorkerError(...))``.

    Ordering rules:
      ``emit_terminal`` does NOT auto-flush pending items.  Callers MUST
      explicitly drain pending first::

          yield from emitter.flush()
          yield from emitter.emit_terminal(err)

      This keeps responsibilities tight and lets callers control the exact
      ordering invariant required by their downstream consumers.
    """

    def __init__(
        self,
        error_translator: Callable[[SanitizedError], OutT],
    ) -> None:
        self._translate = error_translator
        self._pending: list[OutT] = []

    def emit_ok(self, item: OutT) -> None:
        """Append *item* to the pending queue.

        The item will be yielded on the next ``flush()`` call.  Returns None.
        """
        self._pending.append(item)

    def emit_terminal(self, err: SanitizedError) -> Iterable[OutT]:
        """Translate *err* to a terminal Out and yield it (single-item iterable).

        Does NOT flush pending items first.  Callers must call ``flush()``
        before ``emit_terminal`` to preserve event-ordering invariants.
        """
        yield self._translate(err)

    def flush(self) -> Iterable[OutT]:
        """Yield all pending items then clear the queue.

        Uses snapshot-then-clear semantics: a copy is taken before clearing
        so that mutations to ``_pending`` during iteration are safe.  Callers
        should consume the result immediately (e.g. ``yield from emitter.flush()``).
        """
        snapshot = list(self._pending)
        self._pending.clear()
        yield from snapshot
