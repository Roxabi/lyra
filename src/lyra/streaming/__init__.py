"""Stage-axis streaming primitives (Phase 5 — #1282).

Parser Protocol + StateMachine + EventEmitter composed by:
  - lyra.core.cli.cli_streaming_parser.CliStreamingParser  (str → LlmEvent)
  - lyra.core.processors.stream_processor.StreamProcessor  (LlmEvent → RenderEvent)
"""

from .parser import Parser

__all__ = ["Parser"]
