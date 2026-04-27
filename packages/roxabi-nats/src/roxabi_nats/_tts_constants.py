"""Backward-compat shim — constants live in roxabi_contracts.voice.constants."""

from roxabi_contracts.voice.constants import (
    AGENT_TTS_FIELDS as _AGENT_TTS_FIELDS,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from roxabi_contracts.voice.constants import (
    TTS_CONFIG_FIELDS as _TTS_CONFIG_FIELDS,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
