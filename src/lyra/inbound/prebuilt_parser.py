"""Platform-agnostic passthrough parser for pre-built InboundMessage envelopes.

Used by the audio/voice path on both Telegram and Discord where the InboundMessage
is constructed before the pipeline runs (bytes already fetched; PENDING BlobRef set).
Satisfies the WireParser protocol: parse(raw, ctx) returns the pre-built message
unchanged.  Must NOT import discord or aiogram — platform isolation is enforced by
the inbound-no-adapters importlinter contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lyra.inbound.context import InboundContext


class PrebuiltParser:
    """WireParser that returns a pre-built InboundMessage unchanged.

    The ``raw`` argument passed to ``parse`` is expected to be the
    ``InboundMessage`` instance already constructed by the adapter (audio path).
    ``ctx`` is accepted but unused — present only to satisfy the WireParser
    protocol signature.
    """

    def parse(self, raw: Any, ctx: "InboundContext") -> Any:  # noqa: ARG002
        return raw
