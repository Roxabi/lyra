"""Backward-compatibility shim: factory.llm.llm_codec → factory.llm.cli_nats_codec.

Renamed in #1281 (Phase 4). Import from factory.llm.cli_nats_codec in new code.
"""

from factory.llm.cli_nats_codec import CliNatsCodec as LlmCodec  # noqa: F401

__all__ = ["LlmCodec"]
