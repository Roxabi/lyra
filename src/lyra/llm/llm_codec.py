"""Backward-compatibility shim: lyra.llm.llm_codec → lyra.llm.cli_nats_codec.

Renamed in #1281 (Phase 4). Import from lyra.llm.cli_nats_codec in new code.
"""

from lyra.llm.cli_nats_codec import CliNatsCodec as LlmCodec  # noqa: F401

__all__ = ["LlmCodec"]
