# CLAUDE.md — lyra.stt

## Module shape

`__init__.py` IS the module body — no subfiles. All public symbols live there and
are listed in `__all__`.

## Ownership boundaries

### STTProtocol — re-export, not owner

Canonical location: `lyra.core.ports.stt`. `lyra.stt` re-exports `STTProtocol` for
backward compatibility and convenience. Do not duplicate or extend the protocol here;
changes to the interface belong in `core/ports/stt.py`.

### Noise-detection semantics — owned here

`STTNoiseError`, `is_whisper_noise`, and `WHISPER_NOISE_TOKENS` are the single source
of truth for what counts as "empty or noisy transcription". Callers (middleware, agents)
catch `STTNoiseError` to dispatch the `stt_noise` template. They must NOT re-implement
detection inline — raise the error here (via `nats_stt_client`), catch it there.

### MIME helper — owned here

`mime_from_suffix` was relocated from `nats_stt_client` because attachment handlers
receive file paths and need a MIME type before calling `STTProtocol.transcribe(audio,
mime)`. The mapping must stay co-located with the protocol re-export so all attachment
paths resolve MIME consistently.

## Two-tier error model

| Error | Meaning | Raised by |
|---|---|---|
| `STTUnavailableError` | Transport failure (timeout, NATS unreachable) | `nats_stt_client` |
| `STTNoiseError` | Semantic empty/noise — transcription succeeded but output is useless | `nats_stt_client` |

Catch `STTUnavailableError` for retry/fallback logic. Catch `STTNoiseError` to send a
"I didn't catch that" reply. Do not conflate them.

## Active consumers

- `core/hub/middleware/middleware_stt.py` — catches both errors, dispatches templates
- `core/agent/agent.py` — catches `STTNoiseError` in voice turn handling
- `agents/simple_agent.py` — same pattern
- `nats/nats_stt_client.py` — raises both errors; also uses `mime_from_suffix`
- Bootstrap factories — inject `STTProtocol` implementation via `lyra.stt` import

## Adding to this module

New symbols must fit one of the three ownership areas above (protocol convenience,
noise semantics, MIME/attachment helpers). Anything that belongs to the NATS transport
layer goes in `nats_stt_client.py`; anything that belongs to the protocol contract goes
in `core/ports/stt.py`.
