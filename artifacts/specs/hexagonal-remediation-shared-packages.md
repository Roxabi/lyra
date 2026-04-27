---
title: Hexagonal Architecture Remediation — Shared Packages
description: Remediation spec for architectural violations in roxabi-nats, roxabi-contracts, and roxabi-ml-base.
---

# Hexagonal Architecture Remediation — Shared Packages

## Summary Tables

### roxabi-nats

| ID | File | Violation | Priority | Effort |
|----|------|-----------|----------|--------|
| 1 | `_tts_constants.py` | TTS domain knowledge in transport SDK | High | XS |
| 2 | `readiness.py:22` | Lyra-specific subject hardcoded | Medium | XS |
| 3 | `_sanitize.py:14-26` | Platform field names hardcoded in allowlist | Medium | S |
| 4 | `adapter_base.py:31-39` | `DeprecationWarning` fires at module import | High | XS |
| 5 | `_validate.py` vs `_nats_utils.py` | Duplicate validation logic, diverged regex | High | S |

### roxabi-contracts

| ID | File | Violation | Priority | Effort |
|----|------|-----------|----------|--------|
| 6 | `voice/testing.py`, `image/testing.py` | FakeWorkers are full NATS subscribers → circular dep | High | M |
| 7 | `voice/testing.py:57-70`, `image/testing.py:61-74` | `_assert_not_production` / `_assert_loopback_url` duplicated | Medium | XS |
| 8 | `_nats_utils.py` | `validate_worker_id` duplicates transport-layer logic | Medium | S |
| 9 | `voice/builders.py:54,99` | `datetime.now(timezone.utc)` called inline — non-deterministic | Medium | XS |

### roxabi-ml-base

| ID | File | Violation | Priority | Effort |
|----|------|-----------|----------|--------|
| 10 | `.github/workflows/build.yml:11` | `TAG` hardcoded, not derived from `ARG TORCH_VERSION` | Low | XS |

---

## Implementation Order

Cross-package dependency: **5 must land before 8**.

Recommended sequence within each package:

**roxabi-nats:** 4 → 5 → 2 → 3 → 1

**roxabi-contracts:** 7 → 9 → 8 (after 5) → 6

**roxabi-ml-base:** 10 (independent)

---

## Per-Violation Detail

### V1 — `_tts_constants.py`: TTS domain knowledge in transport SDK

**Current state:** `roxabi_nats._tts_constants` defines `_TTS_CONFIG_FIELDS` and `_AGENT_TTS_FIELDS` — tuples of TTS-specific field names. The transport SDK has no business knowing voice domain field names.

**Fix:** Move both tuples to `roxabi_contracts/voice/constants.py` (new file). Update all import sites in `roxabi-nats` and consuming services to import from `roxabi_contracts.voice.constants`. Delete `_tts_constants.py` from `roxabi-nats` and its test `test_tts_constants.py`.

**Acceptance criteria:**
- `roxabi_nats` contains no TTS-domain symbol references
- `roxabi_contracts.voice.constants` exports `TTS_CONFIG_FIELDS`, `AGENT_TTS_FIELDS` (drop underscore prefix — these are now part of a public contract API)
- Existing consumers compile without change after updating imports

---

### V2 — `readiness.py:22`: Lyra-specific NATS subject hardcoded

**Current state:**
```python
READINESS_SUBJECT = "lyra.system.ready"
```
A general-purpose SDK should not hardcode a product-specific subject namespace.

**Fix option A (env override):**
```python
import os
READINESS_SUBJECT = os.getenv("LYRA_READINESS_SUBJECT", "lyra.system.ready")
```

**Fix option B (parameter injection):** Pass `readiness_subject` as a parameter to `start_readiness_responder()` and `wait_for_hub()`, with `READINESS_SUBJECT` as the default.

Recommended: **Option B** — env var coupling is still coupling; parameter injection is testable and makes the default explicit to callers. Keep the module-level constant as the default value only.

**Acceptance criteria:**
- `start_readiness_responder(nc, buses, subject=READINESS_SUBJECT)` — subject injectable
- `wait_for_hub(nc, timeout=..., subject=READINESS_SUBJECT)` — subject injectable
- Existing call sites pass no `subject` arg → behavior unchanged
- `test_readiness.py` tests both default and injected subject

---

### V3 — `_sanitize.py:14-26`: Platform field allowlist hardcoded

**Current state:** `PLATFORM_META_ALLOWLIST` is a frozenset literal inside the transport SDK containing Discord/Telegram field names (`guild_id`, `chat_id`, etc.). The SDK must not encode platform semantics.

**Fix:** Make the allowlist injectable. Change `sanitize_platform_meta` signature:
```python
def sanitize_platform_meta(
    meta: dict[str, Any],
    *,
    allowlist: frozenset[str] = PLATFORM_META_ALLOWLIST,
) -> dict[str, Any]:
```

Move the canonical `PLATFORM_META_ALLOWLIST` definition to `roxabi_contracts` (e.g., `roxabi_contracts.envelope` or a new `roxabi_contracts.meta`). Re-export the frozenset from `roxabi_nats._sanitize` as `PLATFORM_META_ALLOWLIST` for backwards compat, sourced from the contracts import.

**Acceptance criteria:**
- `sanitize_platform_meta` accepts custom allowlist
- Canonical allowlist lives in `roxabi_contracts`
- `roxabi_nats._sanitize` imports allowlist from contracts (¬duplicate definition)
- Existing call sites without explicit `allowlist=` arg pass unchanged

---

### V4 — `adapter_base.py:31-39`: Module-level `DeprecationWarning`

**Current state:**
```python
from roxabi_contracts.envelope import CONTRACT_VERSION

warnings.warn(
    "roxabi_nats.adapter_base.CONTRACT_VERSION ...",
    DeprecationWarning,
    stacklevel=2,
)
```
The warning fires on every `import roxabi_nats.adapter_base` or `import roxabi_nats` (via `__init__`), including in production code that never accesses `CONTRACT_VERSION` through this path.

**Fix:** Remove the module-level import and warn. Replace with `__getattr__` shim at module level:
```python
def __getattr__(name: str):
    if name == "CONTRACT_VERSION":
        import warnings
        from roxabi_contracts.envelope import CONTRACT_VERSION as _CV
        warnings.warn(
            "roxabi_nats.adapter_base.CONTRACT_VERSION is deprecated; "
            "import from roxabi_contracts.envelope. Removed at v0.3.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        return _CV
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

**Acceptance criteria:**
- `import roxabi_nats.adapter_base` does not emit any warning
- `roxabi_nats.adapter_base.CONTRACT_VERSION` emits exactly one `DeprecationWarning`
- `NatsAdapterBase` uses `CONTRACT_VERSION` imported directly from `roxabi_contracts.envelope` (not the shim)
- `test_adapter_base.py` asserts no warning on plain import, one warning on attribute access

---

### V5 — `_validate.py` vs `_nats_utils.py`: Duplicate validation logic

**Current state:**

`roxabi_nats._validate`:
```python
_NATS_IDENT = re.compile(r"[A-Za-z0-9_.\-]+")
def validate_nats_token(value, *, kind, allow_empty=False): ...
```

`roxabi_contracts._nats_utils`:
```python
_SAFE_WORKER_ID_RE = re.compile(r"[A-Za-z0-9_-]+")
def validate_worker_id(worker_id: str) -> None: ...
```

Differences: `_NATS_IDENT` allows `.` (for subject tokens like `lyra.tts.request`); `_SAFE_WORKER_ID_RE` does not (worker IDs must not contain `.` to avoid subject injection). Both validate related concepts with slightly different semantics — the divergence is intentional but undocumented, creating maintenance risk.

**Fix:**
1. Keep `validate_nats_token` in `roxabi_nats._validate` as the canonical implementation for subject/queue-group tokens (allows `.`).
2. Add `validate_worker_id` to `roxabi_nats._validate` with the stricter regex (`[A-Za-z0-9_-]+`, no `.`). Document the distinction inline.
3. `roxabi_contracts._nats_utils` replaces its implementation with an import:
   ```python
   from roxabi_nats._validate import validate_worker_id  # noqa: F401
   ```
4. `roxabi_contracts._nats_utils.py` may be reduced to just the re-export + module docstring, or deleted if the import is inlined into each `subjects.py` that uses it.

**Acceptance criteria:**
- One regex definition per semantic class (subject tokens vs worker ids)
- `validate_worker_id` in `roxabi_contracts` delegates to `roxabi_nats` (no duplicate impl)
- Existing `from roxabi_contracts._nats_utils import validate_worker_id` call sites unchanged
- Test coverage for both: `.`-containing worker_id rejected, `.`-containing subject token accepted

---

### V6 — `voice/testing.py` + `image/testing.py`: FakeWorkers create circular dependency

**Current state:** `FakeTtsWorker`, `FakeSttWorker` (`voice/testing.py`) and `FakeImageWorker` (`image/testing.py`) are full NATS subscriber implementations that import `from roxabi_nats.connect import nats_connect`. This creates:

```
roxabi-contracts[testing] → roxabi-nats → (roxabi-contracts at runtime for envelope)
```

The cycle is currently harmless because `roxabi-nats` imports only `roxabi_contracts.envelope` (not `.testing`), but the structural violation makes future refactoring unsafe and prevents `roxabi-contracts` from ever becoming a zero-dependency schema package.

**Decision needed:**

```
── Decision: FakeWorker extraction ──
Context:     FakeWorkers in roxabi-contracts[testing] import roxabi_nats,
             creating a contracts→transport dependency. Contracts should be
             a pure schema package with no transport knowledge.
Target:      Eliminate the circular dependency potential; make contracts
             importable without any NATS transport dependency.
Path:        Move FakeWorkers to a location that may freely depend on both
             roxabi-nats and roxabi-contracts.

Options:
  A. Extract to a new `roxabi-contracts-testkit` package (separate repo/package)
     — roxabi-contracts becomes zero-dep on nats; testkit depends on both.
     Downside: new package to publish, version-pin in consumers.
  B. Move FakeWorkers into roxabi-nats as a `[testing]` extra   ← recommended
     — roxabi-nats already depends on nats-py; FakeWorkers belong to the
     transport layer. Contracts[testing] becomes a thin re-export shim
     pointing to roxabi_nats.testing. No new package.
Recommended: Option B — minimal new surface, natural ownership (transport fakes
             live in the transport package), contracts stays schema-only.
```

**Fix (Option B):**
1. Create `roxabi_nats/testing/` subpackage with `voice.py` and `image.py` containing the moved worker classes.
2. Add `[testing]` extra to `roxabi-nats` `pyproject.toml` (nats-py already in main deps; extra may be empty or add pytest-asyncio).
3. Update `roxabi_contracts/voice/testing.py` and `image/testing.py` to re-export from `roxabi_nats.testing`:
   ```python
   from roxabi_nats.testing.voice import FakeTtsWorker, FakeSttWorker  # noqa: F401
   ```
   Keep the file for backwards-compat import paths; deprecate at next minor.
4. Move Guard 1 tripwire comment to `roxabi_nats/testing/__init__.py`.

**Acceptance criteria (Option B):**
- `roxabi_contracts` has no `import roxabi_nats` in non-testing source files
- `FakeTtsWorker`, `FakeSttWorker`, `FakeImageWorker` importable from both old and new paths
- Old import paths emit `DeprecationWarning`
- Guard 2 (env) and Guard 3 (loopback) tests pass unchanged

---

### V7 — `_testing_guards.py` doesn't exist: duplicated guard functions

**Current state:** `_assert_not_production` and `_assert_loopback_url` (including `ALLOWED_LOOPBACK_HOSTS`) are copy-pasted identically in both `voice/testing.py:57-70` and `image/testing.py:61-74`.

**Fix (immediate quick win, independent of V6):** Extract to `roxabi_contracts/_testing_guards.py`:
```python
# roxabi_contracts/_testing_guards.py
ALLOWED_LOOPBACK_HOSTS: frozenset[str] = frozenset(...)
def _assert_not_production(cls_name: str) -> None: ...
def _assert_loopback_url(url: str) -> None: ...
```
Both `voice/testing.py` and `image/testing.py` import from there.

If V6 Option B lands, migrate `_testing_guards.py` into `roxabi_nats/testing/_guards.py` and re-export from contracts for the deprecation window.

**Acceptance criteria:**
- Guard functions defined exactly once
- Both `voice/testing.py` and `image/testing.py` import from the shared module
- Guard behavior tests unchanged

---

### V8 — `_nats_utils.py`: `validate_worker_id` duplicates transport logic

**Blocked on V5.** After V5 lands `validate_worker_id` in `roxabi_nats._validate`:

**Fix:** Replace the implementation in `roxabi_contracts/_nats_utils.py` with:
```python
from roxabi_nats._validate import validate_worker_id as validate_worker_id
```

**Acceptance criteria:**
- `roxabi_contracts._nats_utils` contains no regex definition
- All existing call sites (`from roxabi_contracts._nats_utils import validate_worker_id`) unchanged
- `validate_worker_id` behavior identical to pre-migration (same test suite passes)

---

### V9 — `voice/builders.py:54,99`: Non-deterministic `datetime.now()` in builders

**Current state:**
```python
issued_at=datetime.now(timezone.utc),
```
Called inline in `build_stt_response` and `build_tts_response`. Testing requires mocking `datetime.now` at the module level — brittle and framework-dependent.

**Fix:** Add an injectable `issued_at` parameter with a sentinel default:
```python
def build_tts_response(
    payload: dict[str, Any],
    *,
    ok: bool,
    ...,
    issued_at: datetime | None = None,
) -> str:
    response = TtsResponse(
        ...,
        issued_at=issued_at if issued_at is not None else datetime.now(timezone.utc),
    )
```
Same for `build_stt_response`. Callers that omit `issued_at` get the current time as before.

**Acceptance criteria:**
- Both builders accept `issued_at: datetime | None = None`
- Tests can pass a fixed `datetime` without mocking the clock
- Existing call sites with no `issued_at` arg produce a response with a current timestamp
- `test_voice_builders.py` includes a deterministic timestamp test (no `unittest.mock.patch`)

---

### V10 — `build.yml:11`: CI `TAG` hardcoded

**Current state:**
```yaml
env:
  TAG: cu128-py312-torch2.7.1
```
The Dockerfile declares `ARG TORCH_VERSION=2.7.1`; the CI tag is a manual concatenation that will silently drift if the ARG is updated.

**Fix:** Derive the tag from Dockerfile ARG values at CI run time:
```yaml
- name: Derive image tag from Dockerfile ARGs
  id: tag
  run: |
    TORCH_VERSION=$(grep 'ARG TORCH_VERSION=' Dockerfile | cut -d= -f2)
    PYTHON_VERSION=312   # or parse from base image label if needed
    echo "tag=cu128-py${PYTHON_VERSION}-torch${TORCH_VERSION}" >> "$GITHUB_OUTPUT"

- name: Build and push
  uses: docker/build-push-action@v6
  with:
    tags: |
      ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:${{ steps.tag.outputs.tag }}
      ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest
```
Remove the `TAG` env var from the top-level `env:` block.

**Acceptance criteria:**
- CI tag is never hand-edited; always computed from `ARG TORCH_VERSION`
- Changing `ARG TORCH_VERSION` in `Dockerfile` automatically changes the published tag
- `latest` tag still published alongside the versioned tag
