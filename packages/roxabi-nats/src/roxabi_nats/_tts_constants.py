"""Backward-compat shim — constants live in roxabi_contracts.voice.constants."""

from roxabi_contracts.voice.constants import (
    AGENT_TTS_FIELDS as _AGENT_TTS_FIELDS,
)
from roxabi_contracts.voice.constants import (
    TTS_CONFIG_FIELDS as _TTS_CONFIG_FIELDS,
)

__all__ = ["_AGENT_TTS_FIELDS", "_TTS_CONFIG_FIELDS"]
