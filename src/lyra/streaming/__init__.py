"""Stage-axis streaming primitives (Phase 5 — #1282).

Parser Protocol + StateMachine + EventEmitter composed by:
  - lyra.core.cli.cli_streaming_parser.CliStreamingParser  (str → LlmEvent)
  - lyra.core.processors.stream_processor.StreamProcessor  (LlmEvent → RenderEvent)
"""

from .event_emitter import EventEmitter
from .parser import Parser
from .state_machine import StateMachine

__all__ = ["EventEmitter", "Parser", "StateMachine"]
