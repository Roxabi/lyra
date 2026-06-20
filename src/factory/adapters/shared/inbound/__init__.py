"""Inbound glue kit for platform adapters (#1931, subpackage split #1962)."""

from factory.adapters.shared.inbound.context import (
    build_discord_inbound_ctx,
    build_telegram_inbound_ctx,
)
from factory.adapters.shared.inbound.pipeline import (
    InboundPipelineKit,
    get_inbound_pipeline_kit,
    get_or_create_parser,
    reset_inbound_pipeline_kit,
    run_inbound_guarded,
)
from factory.adapters.shared.inbound.platform_meta import cancel_typing_for_inbound
from factory.adapters.shared.inbound.typing_shim import (
    cancel_typing_shim,
    start_typing_shim,
)

__all__ = [
    "InboundPipelineKit",
    "build_discord_inbound_ctx",
    "build_telegram_inbound_ctx",
    "cancel_typing_for_inbound",
    "cancel_typing_shim",
    "get_inbound_pipeline_kit",
    "get_or_create_parser",
    "reset_inbound_pipeline_kit",
    "run_inbound_guarded",
    "start_typing_shim",
]
