# roxabi-contracts

Shared Pydantic schemas for Lyra cross-project NATS contracts. Per-domain submodules (voice, image, memory, llm) import `ContractEnvelope` from this package as their common base. Extracted from Lyra as a uv workspace subpackage per [ADR-049](../../docs/architecture/adr/049-roxabi-contracts-shared-schema-package.mdx).

## Install (external projects)

```toml
[tool.uv.sources]
roxabi-contracts = {
  git = "https://github.com/Roxabi/lyra.git",
  subdirectory = "packages/roxabi-contracts",
  tag = "roxabi-contracts/v0.1.0"
}
```

## Public API contract

The stable external contract is defined by `__all__` in `roxabi_contracts/__init__.py`. v0.1.0 ships:

- `ContractEnvelope` — base Pydantic model for all per-domain contract schemas

Future domain submodules (voice, image, memory, llm) arrive in subsequent tags. See ADR-049 §Versioning for SemVer rules.

## Voice domain

First per-domain contract, ported from ADR-044 (`lyra` + `voiceCLI` Tts/Stt
wire format). Import surface:

```python
from roxabi_contracts.voice import (
    SUBJECTS,
    TtsRequest,
    TtsResponse,
    SttRequest,
    SttResponse,
)
```

### Subjects

`SUBJECTS` is a frozen namespace exposing:

- `SUBJECTS.tts_request` → `"lyra.voice.tts.request"`
- `SUBJECTS.tts_heartbeat` → `"lyra.voice.tts.heartbeat"`
- `SUBJECTS.stt_request` → `"lyra.voice.stt.request"`
- `SUBJECTS.stt_heartbeat` → `"lyra.voice.stt.heartbeat"`
- `SUBJECTS.tts_workers` → `"tts_workers"` (queue group)
- `SUBJECTS.stt_workers` → `"stt_workers"` (queue group)

Helpers: `per_worker_tts(worker_id)` and `per_worker_stt(worker_id)` return the per-worker addressing subject (`"{subject}.{worker_id}"`).

### Models

All four models subclass `ContractEnvelope` and inherit its
`ConfigDict(extra="ignore")` forward-compat invariant.

| Model | Purpose |
|---|---|
| `TtsRequest` | Synthesis request from hub to voice worker |
| `TtsResponse` | Synthesis reply; on success carries `audio_b64` + `mime_type` + `duration_ms` |
| `SttRequest` | Transcription request (audio bytes in base64) |
| `SttResponse` | Transcription reply; on success carries `text` + `language` + `duration_seconds` |

### No-transport invariant

`roxabi_contracts.voice` imports no NATS transport code. The submodule is
pure Pydantic and safe to pull from any consumer — including environments
that do not install `nats-py`. The `fixtures` submodule (synthesized
`silence_wav_16khz` + `sample_transcript_en`) is test-only and is NOT
re-exported from `roxabi_contracts.voice`; import it explicitly:

```python
from roxabi_contracts.voice.fixtures import silence_wav_16khz, sample_transcript_en
```

`scipy` is declared in `[project.optional-dependencies].testing` and is
only pulled when a consumer requests the `[testing]` extra.
