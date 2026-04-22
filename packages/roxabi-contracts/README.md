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

## Satellite pin freshness (Renovate)

Satellites pin `roxabi-contracts` (and `roxabi-nats`) by git tag. Without an automated pin-freshness rule, pins silently drift as new tags are cut and the SDK → satellite → hub triangle ships mixed versions. Every satellite repo MUST add the following Renovate rule to `renovate.json`:

```json5
// renovate.json — satellite repos (voiceCLI, imageCLI, roxabi-vault, …)
{
  "packageRules": [{
    "matchDatasources": ["git-refs"],
    "matchSourceUrls": ["https://github.com/Roxabi/lyra"],
    "matchPackageNames": ["roxabi-nats", "roxabi-contracts"],
    "groupName": "roxabi sdk",
    "schedule": ["before 6am on monday"]
  }]
}
```

### Why `matchDatasources: ["git-refs"]` is mandatory

Renovate resolves git-sourced `uv` pins through the **`git-refs`** datasource, NOT the default `pypi` datasource. Omit this field and the rule silently does not fire — Renovate matches nothing, no PR is opened, and the pin stays stale indefinitely. This is the single most common misconfiguration; prominent so it is not repeated.

### Why both packages in one rule

`roxabi-nats` (transport) and `roxabi-contracts` (schemas) form a single coordinated SDK. Grouping them under `groupName: "roxabi sdk"` prevents partial upgrades — e.g., bumping `roxabi-contracts` past a `CONTRACT_VERSION` migration while leaving `roxabi-nats` on an older release, which would produce a version mismatch at envelope parse time. One grouped PR per week per satellite keeps the two coordinates in lockstep.

### Why the weekly Monday schedule

`before 6am on monday` gives a satellite a **stability window**: a contributor merging on Friday has the weekend before the next Renovate wave, and the Monday-morning PR lands before the week's work begins. Batching also avoids PR noise — SDK tags may cut mid-week but satellites see them consolidated once, not piecemeal.

### End-state

Renovate reads the `tag = "..."` pin in `[tool.uv.sources]`, observes a newer tag on the upstream repo, and opens a PR each Monday. Without this rule, pin freshness is purely documentation and drift becomes inevitable (ADR-049 §Satellite pin freshness).

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

## Test doubles

`roxabi_contracts.voice.testing` provides in-process replacements for a real
voiceCLI satellite (`FakeTtsWorker`, `FakeSttWorker`) — intended for lyra hub
tests and voiceCLI adapter tests that need to exercise the NATS request/reply
cycle without a GPU or real model.

Install with the `[testing]` optional extra:

```bash
uv pip install "roxabi-contracts[testing]"
```

Three non-bypassable guards prevent production contamination
(see [ADR-049 §Test-double pattern](../../docs/architecture/adr/049-roxabi-contracts-shared-schema-package.mdx)):

1. **Import-time gate.** `voice.testing` imports `nats` at module top. A
   bare `roxabi-contracts` install (no `[testing]` extra) fails with
   `ModuleNotFoundError: No module named 'nats'` before any runtime code runs.
2. **Environment assertion.** `__init__` raises `RuntimeError` when
   `LYRA_ENV=production`. No override flag.
3. **Loopback-only URL.** `start()` raises `ValueError` on any non-loopback
   NATS URL (`127.0.0.1`, `localhost`, `::1`, `0:0:0:0:0:0:0:1` are the only
   accepted hosts). No override.
