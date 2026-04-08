"""Shared TTS field constants for the NATS request/response boundary.

Two separate tuples because each side of the boundary has different needs:
- ``_TTS_CONFIG_FIELDS`` — fields NatsTtsClient serializes from AgentTTSConfig
  into the outgoing request. voice/language are passed as explicit keyword
  arguments to synthesize(); default_language/languages are not forwarded.
- ``_AGENT_TTS_FIELDS`` — fields TtsAdapterStandalone reads back from the
  request payload into a lightweight _NatsTtsConfig stand-in.

Both are defined here so that additions to AgentTTSConfig only require a
single-file update.
"""

# Hub side — serialised by NatsTtsClient.synthesize()
_TTS_CONFIG_FIELDS: tuple[str, ...] = (
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
_AGENT_TTS_FIELDS: tuple[str, ...] = (
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
