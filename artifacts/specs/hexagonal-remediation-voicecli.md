---
title: "voiceCLI Hexagonal Architecture Remediation"
description: "Remediation spec for hexagonal architecture violations in voiceCLI — ports extraction, adapter decoupling, importlinter activation."
---

# voiceCLI Hexagonal Architecture Remediation

## Summary Table

| ID | File | Fix | Est. Lines Changed | Priority |
|----|------|-----|--------------------|----------|
| P0-1 | `engine.py` | Move `TTSEngine` ABC → `ports/tts.py`; rename/keep infra in `engine.py` | ~40 | P0 |
| P0-2 | `transcribe.py:63` | Centralize `SOCKET_PATH` + path construction → `paths.py` | ~30 | P0 |
| P0-3 | `api.py:398-427` | Introduce `SynthesisPort`; daemon + direct-engine become adapters | ~80 | P0 |
| P1-4 | _(missing)_ | Add `STTEnginePort` Protocol → `ports/stt.py` | ~30 | P1 |
| P1-5 | `cli.py:441` | Extract `HISTORY_PATH` → `paths.py`; `_write_clipboard` → `utils.py` | ~20 | P1 |
| P1-6 | `nats/stt_adapter.py:244` | Add `api.warmup_model(model)` public fn; adapter calls that | ~15 | P1 |
| P1-7 | `cli.py` (1548 lines) | Extract NATS bootstrap, `doctor`, `dictate`, `samples` to own modules | ~400 moved | P1 |
| P1-8 | `.importlinter` | Activate layer contracts covering current topology | ~30 | P1 |

---

## P0-1 — `TTSEngine` ABC co-located with Infrastructure

### Current State

`engine.py` mixes three concerns:
- `TTSEngine` ABC — domain port (17 lines)
- `check_vram`, `cuda_guard` — infrastructure utilities
- `get_engine`, `_get_registry` — factory / infrastructure wiring

Any module importing the ABC transitively pulls in infrastructure.

### Target State

```
voicecli/ports/tts.py       ← TTSEngine ABC only
voicecli/engine.py          ← OR rename to engine_factory.py
                               imports from ports/tts.py
                               retains check_vram, cuda_guard, get_engine, _get_registry
```

`ports/tts.py` has zero infrastructure imports.

### Acceptance Criteria

- `from voicecli.ports.tts import TTSEngine` has no transitive infra import
- `engine.py` (or `engine_factory.py`) imports `TTSEngine` from `ports.tts`
- All existing call sites updated to import ABC from `ports.tts`
- `pyright` clean on changed files

---

## P0-2 — `SOCKET_PATH` scattered across 3 modules

### Current State

| Location | Issue |
|----------|-------|
| `transcribe.py:63` | imports `SOCKET_PATH` from `stt_daemon` |
| `daemon.py` | constructs socket path independently |
| `stt_daemon.py` | owns `SOCKET_PATH`, duplicates construction |

Three sources of truth → desync risk.

### Target State

```
voicecli/paths.py
  SOCKET_PATH: Path          ← single definition
  HISTORY_PATH: Path         ← (also needed for P1-5)
  [any other runtime paths]
```

`stt_daemon.py`, `daemon.py`, `transcribe.py`, `cli.py` all import from `paths.py`. No module constructs socket paths inline.

### Acceptance Criteria

- `paths.py` is the single import source for all runtime path constants
- `grep -r "SOCKET_PATH" src/` returns only `paths.py` (definition) + import lines
- No inline path construction duplicated across modules

---

## P0-3 — `api.py` coupled to `daemon.py` transport + `_skip_daemon` flag

### Current State

`api.py:398-427`:
- imports `daemon.py` (Unix socket transport) directly
- imports `model_registry` directly
- `_skip_daemon: bool` parameter routes between daemon and direct-engine path inside the application layer

Application layer knows about transport alternatives — violates hexagonal boundary.

### Target State

```
voicecli/ports/synthesis.py
  class SynthesisPort(Protocol):
      async def synthesize(self, text: str, **opts) -> AudioResult: ...

voicecli/adapters/synthesis_daemon.py    ← wraps daemon.py socket transport
voicecli/adapters/synthesis_direct.py   ← wraps engine directly (was _skip_daemon=True path)

api.py
  synthesize(port: SynthesisPort, text: str, ...) -> AudioResult
  ← ¬imports daemon.py | ¬imports model_registry | ¬_skip_daemon flag
```

Adapter selection happens at bootstrap/DI layer (e.g., `cli.py` or `nats/`), not in `api.py`.

### Acceptance Criteria

- `api.py` imports only `SynthesisPort` from `ports.synthesis`; zero import of `daemon`, `model_registry`
- `_skip_daemon` parameter removed from all application-layer functions
- Both adapters pass same integration test suite against `SynthesisPort` contract
- `pyright` protocol compliance check passes on both adapter classes

---

## P1-4 — No `STTEngine` port

### Current State

`transcribe.transcribe()` called directly by:
- `stt_daemon.py`
- `nats/stt_adapter.py`
- `api.py`

No protocol boundary — callers coupled to implementation module.

### Target State

```
voicecli/ports/stt.py
  class STTEnginePort(Protocol):
      def transcribe(
          self,
          audio: bytes | Path,
          model: str,
          language: str | None,
          **opts,
      ) -> TranscriptionResult: ...

      def warmup(self, model: str) -> None: ...
```

`TranscriptionResult` dataclass/TypedDict defined in `ports/stt.py` or `voicecli/models.py`.

All callers depend on `STTEnginePort`; concrete impl is an adapter.

### Acceptance Criteria

- `STTEnginePort` Protocol in `ports/stt.py` with `transcribe` + `warmup` signatures
- `stt_daemon`, `nats/stt_adapter`, `api` import port only (not `transcribe` module directly)
- Concrete `WhisperSTTAdapter` (or equivalent) implements protocol; `pyright` confirms structural subtype

---

## P1-5 — `cli.py` imports private internals from `stt_daemon`

### Current State

`cli.py:441`:
- imports `HISTORY_PATH` from `stt_daemon` (infrastructure constant — wrong owner)
- imports `_write_clipboard` from `stt_daemon` (private utility — leaked boundary)

### Target State

```
voicecli/paths.py           ← HISTORY_PATH (alongside SOCKET_PATH from P0-2)
voicecli/utils.py           ← write_clipboard(text: str) -> None  (public, no leading _)
```

`stt_daemon` delegates to `utils.write_clipboard`; `cli.py` imports from `utils`.

### Acceptance Criteria

- `HISTORY_PATH` defined only in `paths.py`
- `write_clipboard` is public in `utils.py`; `stt_daemon` and `cli` both import from there
- No `_write_clipboard` references remain in codebase

---

## P1-6 — `nats/stt_adapter.py` imports private `transcribe._load_model`

### Current State

`nats/stt_adapter.py:244` calls `transcribe._load_model(...)` — private function, implementation detail of transcription module.

### Target State

```
voicecli/api.py
  def warmup_model(model: str) -> None: ...   ← public; calls _load_model internally
```

`nats/stt_adapter` calls `api.warmup_model(model)`. `_load_model` remains private to `transcribe.py`.

### Acceptance Criteria

- `api.warmup_model` is the sole public entry point for model pre-loading
- `grep -r "_load_model" src/` returns only `transcribe.py` (definition + internal calls)
- NATS adapter integration test pre-warms model via `api.warmup_model`

---

## P1-7 — `cli.py` god object (1548 lines)

### Current State

`cli.py` owns: NATS TTS serve, NATS STT serve, `doctor` command, `dictate` sub-app, `samples` sub-app, main Typer app wiring, option parsing, logging setup.

### Target State

```
voicecli/
  cli.py                   ← main app + logging setup + top-level command registration (~300 lines)
  cli_doctor.py            ← doctor command group
  cli_dictate.py           ← dictate sub-app (Typer sub-application)
  cli_samples.py           ← samples sub-app
  nats/__init__.py         ← nats_serve_tts(), nats_serve_stt() bootstrap (move from cli.py)
```

`cli.py` imports and registers sub-apps; it does not implement their logic.

### Acceptance Criteria

- `cli.py` ≤ 300 lines (within project file-length gate)
- Each extracted module ≤ 300 lines
- All existing CLI commands reachable via same invocation paths (`voicecli doctor`, `voicecli dictate ...`, etc.)
- `uv run pytest` green after extraction

---

## P1-8 — `.importlinter` contracts commented out

### Current State

All contracts in `.importlinter` are disabled — no enforcement of layer boundaries.

### Target State

Activate a base contract matching current (post-remediation) topology, then add violation-specific contracts as each P0/P1 is fixed.

#### Stanza to activate

```ini
[importlinter]
root_package = voicecli

[importlinter:contract:layer-boundaries]
name = Layer boundaries
type = layers
layers =
    voicecli.cli
    voicecli.nats
    voicecli.api
    voicecli.adapters
    voicecli.ports
    voicecli.models
independence_clause = voicecli.ports
    does not import from voicecli.adapters
    does not import from voicecli.api
    does not import from voicecli.nats
    does not import from voicecli.cli

[importlinter:contract:no-private-cross-import]
name = No private cross-module imports
type = forbidden
source_modules =
    voicecli.nats
    voicecli.cli
forbidden_modules =
    voicecli.transcribe
    voicecli.stt_daemon
allow_indirect_imports = False
```

Add contract stanzas incrementally as violations are resolved. Keep contract commented in feature branch until its fix lands; uncomment in same PR.

### Acceptance Criteria

- `uv run lint-imports` passes on CI with at minimum `layer-boundaries` and `no-private-cross-import` active
- Each remediation PR activates its corresponding contract stanza

---

## Ports Catalogue

All ports live under `voicecli/ports/`. Zero infrastructure imports allowed in any port file.

### `ports/tts.py` — `TTSEnginePort`

```python
from abc import ABC, abstractmethod
from pathlib import Path

class TTSEnginePort(ABC):
    @abstractmethod
    def synthesize(self, text: str, speaker: str | None, **kwargs) -> bytes: ...

    @abstractmethod
    def list_speakers(self) -> list[str]: ...

    @abstractmethod
    def is_available(self) -> bool: ...
```

### `ports/stt.py` — `STTEnginePort`

```python
from typing import Protocol
from dataclasses import dataclass
from pathlib import Path

@dataclass
class TranscriptionResult:
    text: str
    language: str | None
    segments: list[dict]  # refine to typed segment if needed

class STTEnginePort(Protocol):
    def transcribe(
        self,
        audio: bytes | Path,
        model: str,
        language: str | None = None,
        **opts,
    ) -> TranscriptionResult: ...

    def warmup(self, model: str) -> None: ...
```

### `ports/synthesis.py` — `SynthesisPort`

```python
from typing import Protocol
from dataclasses import dataclass

@dataclass
class AudioResult:
    audio: bytes
    sample_rate: int
    format: str

class SynthesisPort(Protocol):
    async def synthesize(
        self,
        text: str,
        speaker: str | None = None,
        **opts,
    ) -> AudioResult: ...
```

### `ports/clipboard.py` — `ClipboardPort`

```python
from typing import Protocol

class ClipboardPort(Protocol):
    def write(self, text: str) -> None: ...
    def read(self) -> str: ...
```

### `ports/history.py` — `HistoryRepository`

```python
from typing import Protocol
from pathlib import Path
from datetime import datetime

class HistoryEntry:
    text: str
    timestamp: datetime

class HistoryRepository(Protocol):
    def append(self, entry: HistoryEntry) -> None: ...
    def read_all(self) -> list[HistoryEntry]: ...
    def path(self) -> Path: ...
```

---

## Implementation Order

Dependencies determine order: ports must exist before adapters; adapters before application rewiring; application rewiring before CLI extraction; importlinter last.

```
Phase 1 — Ports foundation (unblocks everything)
  1a. Create voicecli/paths.py            ← P0-2, P1-5 unblocked
  1b. Create voicecli/ports/tts.py        ← P0-1 unblocked
  1c. Create voicecli/ports/stt.py        ← P1-4 unblocked
  1d. Create voicecli/ports/synthesis.py  ← P0-3 unblocked
  1e. Create voicecli/ports/clipboard.py  ← P1-5 unblocked
  1f. Create voicecli/ports/history.py    ← P1-5 unblocked

Phase 2 — Centralize paths + fix private imports (P0-2, P1-5, P1-6)
  2a. Populate paths.py; update stt_daemon, daemon, transcribe, cli imports
  2b. Add utils.write_clipboard; update stt_daemon + cli
  2c. Add api.warmup_model; update nats/stt_adapter

Phase 3 — Port adoption in engine layer (P0-1, P1-4)
  3a. engine.py: move ABC to ports/tts.py; update all call sites
  3b. Implement STTEnginePort concrete adapter (WhisperSTTAdapter or equivalent)
  3c. Update stt_daemon, nats/stt_adapter, api to use STTEnginePort

Phase 4 — SynthesisPort + adapter split (P0-3)
  4a. Implement synthesis_daemon.py adapter
  4b. Implement synthesis_direct.py adapter
  4c. Rewrite api.py synthesize path to accept SynthesisPort; remove _skip_daemon

Phase 5 — CLI decomposition (P1-7)
  5a. Extract nats_serve_tts / nats_serve_stt → nats/__init__.py
  5b. Extract doctor → cli_doctor.py
  5c. Extract dictate → cli_dictate.py
  5d. Extract samples → cli_samples.py
  5e. Verify cli.py ≤ 300 lines

Phase 6 — Importlinter activation (P1-8)
  6a. Add base stanzas to .importlinter
  6b. Activate one contract per resolved violation
  6c. CI gate: uv run lint-imports in pre-push hook
```

---

## Definition of Done

### P0 tier — required before merge to main

- [ ] `ports/tts.py`, `ports/stt.py`, `ports/synthesis.py` exist with correct signatures
- [ ] `engine.py` imports `TTSEngine` from `ports/tts.py`; zero domain logic remains in infra modules
- [ ] `paths.py` is sole source for `SOCKET_PATH` and `HISTORY_PATH`
- [ ] `api.py` has zero direct imports of `daemon`, `model_registry`; `_skip_daemon` removed
- [ ] `SynthesisPort` protocol: both adapter implementations pass structural type check
- [ ] `pyright` clean on all changed files
- [ ] `uv run pytest` green

### P1 tier — required before next minor release

- [ ] `STTEnginePort` adopted by all three callers
- [ ] `write_clipboard` public in `utils.py`; no `_write_clipboard` references
- [ ] `api.warmup_model` is sole public warmup entry point
- [ ] `cli.py` ≤ 300 lines; all commands reachable
- [ ] `uv run lint-imports` passes with `layer-boundaries` + `no-private-cross-import` active
- [ ] `ports/clipboard.py` and `ports/history.py` exist (may be unused by concrete impls until follow-up)
