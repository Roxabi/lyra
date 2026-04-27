"""TTS field constants shared across the hub/adapter boundary."""

# Hub side — serialised by NatsTtsClient.synthesize()
TTS_CONFIG_FIELDS: tuple[str, ...] = (
    "engine",
    "accent",
    "personality",
    "speed",
    "emotion",
    "exaggeration",
    "cfg_weight",
    "segment_gap",
    "crossfade",
    "chunk_size",
)

# Adapter side — deserialised by TtsAdapterStandalone.handle()
AGENT_TTS_FIELDS: tuple[str, ...] = (
    "engine",
    "voice",
    "language",
    "accent",
    "personality",
    "speed",
    "emotion",
    "exaggeration",
    "cfg_weight",
    "segment_gap",
    "crossfade",
    "chunk_size",
    "default_language",
    "languages",
)
