# Code Smells Audit — Partition P10: `packages/**/*.py`

**Date:** 2026-05-26
**Scope:** roxabi-blobs, roxabi-contracts, roxabi-nats (`src/` only; tests excluded)
**Context:** First full packages audit; prior 2026-05-18 audit left `packages/` unaudited.

---

## Summary

- **2 source files exceed the 300-line cap without exemption** (`fs_store.py` 344, `_serialize.py` 369); neither is listed in `tools/file_exemptions.txt`.
- **3 severe DRY violations** in `roxabi_nats.testing`: FakeTtsWorker/FakeSttWorker are ~80% copy-paste (~200 shared lines), and FakeImageWorker repeats the same pattern (~110 shared lines).
- **2 god classes/modules** identified: `FsBlobStore` (8 responsibilities) and `NatsAdapterBase` (9 responsibilities).

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `packages/roxabi-blobs/src/roxabi_blobs/fs_store.py` | 1 | High | 344 lines — exceeds 300-line cap without exemption. God class: handles file I/O, SQLite DDL/DML, path-traversal guard, async locking, deduplication, content hashing, ref-counting, and orphan cleanup in a single class. | Split into `BlobFileManager` (fsync + shard paths), `BlobManifest` (SQLite), and `BlobLifecycleGuard` (traversal + lock). File cap will drop naturally once split. |
| `packages/roxabi-nats/src/roxabi_nats/_serialize.py` | 1 | Medium | 369 lines — exceeds 300-line cap without exemption. While well-factored into small helpers, the module has grown past cap due to encode/decode union + pydantic + dataclass + bytes + datetime branches. | Extract `_decode_union` and `_decode_concrete` into a submodule (`_serialize_decode.py`) or extract `_get_hints` + cache logic into `_resolver.py`. Target <300. |
| `packages/roxabi-nats/src/roxabi_nats/testing/voice.py` | 52 | High | FakeTtsWorker and FakeSttWorker share ~80% identical scaffolding: `__init__`, `with_worker_error`, `start`, `stop`, and the first half of `_dispatch`. Only the response model and 2–3 field assignments differ. 267 lines of near-duplication. | Extract a `FakeNatsWorkerBase` or `BaseFakeWorker` ABC in `roxabi_nats.testing.base` with generic `start`/`stop`/`with_worker_error`/`_dispatch` hooks; subclasses inject response builder. |
| `packages/roxabi-nats/src/roxabi_nats/testing/image.py` | 53 | High | FakeImageWorker duplicates the exact same FakeWorker pattern (start/stop/dispatch/heartbeat) already cloned in voice.py. ~110 lines of structural duplication across packages. | Same as above: once `BaseFakeWorker` exists, FakeImageWorker should subclass it and inject `ImageResponse` builder. |
| `packages/roxabi-blobs/src/roxabi_blobs/http_store.py` | 37 | Medium | ASGI lifespan test seams (`_start_asgi_lifespan`, `_stop_asgi_lifespan`) are embedded in production `HttpBlobStore`. This is feature envy — the HTTP client knows about ASGI internals, `lifespan.startup`, and `receive_queue` wiring. | Move ASGI lifespan into a test-only wrapper class (e.g. `TestHttpBlobStore(HttpBlobStore)`) or a pytest fixture in `tests/`. Production `HttpBlobStore` should only hold `_client` + `_transport`. |
| `packages/roxabi-contracts/src/roxabi_contracts/voice/builders.py` | 20 | Low | `build_stt_response` and `build_tts_response` share identical payload-extraction scaffolding (`request_id = payload["request_id"]`; `trace_id = payload.get("trace_id") or request_id`; `issued_at` default logic). ~12 lines of copy-paste. | Extract a `_request_context(payload, issued_at)` helper that returns `(request_id, trace_id, issued_at)` tuple. Also apply to `llm/builders.py` (`build_llm_response`/`build_llm_chunk`). |
| `packages/roxabi-nats/src/roxabi_nats/connect.py` | 20 | Low | `_read_nkey_seed` and `_build_tls_context` share the same 30-line file-read safety dance (`os.open(...O_NOFOLLOW)`, `os.fstat`, `os.fdopen`, OSError/errno handling, empty-file guard). | Extract `_read_secret_file(path: str, kind: str) -> str` helper that performs the TOCTOU-safe read; both functions delegate to it. |
| `packages/roxabi-nats/src/roxabi_nats/adapter_base.py` | 82 | Medium | `NatsAdapterBase` is a borderline god class with 9 responsibilities: connection lifecycle, heartbeat, dispatch, envelope validation, shutdown, readiness probe, health reporting, signal handling, inbox prefix management. | Split heartbeat + health into a mixin or helper (`AdapterHealthMixin`). Keep `NatsAdapterBase` focused on NATS subscription + dispatch + envelope validation only. |

---

## Metrics

| Metric | Count | Notes |
|--------|-------|-------|
| Total `src/**/*.py` files audited | 45 | Excludes tests, scripts, conftest |
| Files >300 lines (unexempted) | 2 | `fs_store.py` (344), `_serialize.py` (369) |
| Files >300 lines with exemption | 0 | No packages files in `tools/file_exemptions.txt` |
| Functions >100 lines | 0 | AST scan returned empty |
| God classes/modules (>5 responsibilities) | 2 | `FsBlobStore`, `NatsAdapterBase` |
| DRY violations (copy-paste >3 lines, ≥2 files) | 4 | FakeWorkers (×3), builders (×2), connect file-read (×2) |
| Cognitive complexity >15 | 0 | No function exceeds threshold; highest is `_compare` in `check_codes_sync.py` (~14, already `# noqa: C901`) |
| Feature envy / misplaced logic | 1 | ASGI lifespan in `HttpBlobStore` |
| Percentage of source files with smells | 9% | 4 of 45 files have High-severity findings |

---

## Recommendations (prioritized)

1. **Extract `BaseFakeWorker` in `roxabi_nats.testing`** — eliminates ~220 lines of duplication across `FakeTtsWorker`, `FakeSttWorker`, and `FakeImageWorker`. This is the highest-value single refactor; the pattern is stable and has three instances.

2. **Split `FsBlobStore` into `BlobFileManager` + `BlobManifest` + `BlobLifecycleGuard`** — reduces the god-class surface, brings the file under 300 lines, and makes SQLite and FS concerns independently testable.

3. **Move ASGI lifespan out of `HttpBlobStore` production path** — create a test-only subclass or fixture. Removes feature envy and shrinks `http_store.py` by ~50 lines.

4. **Extract `_request_context` helper and apply to `voice/builders.py` + `llm/builders.py`** — small, low-risk DRY fix that also makes future domain builders cheaper to add.

5. **Add `tools/file_exemptions.txt` entries OR shrink `_serialize.py` below 300** — if decode-path extraction is deferred, add a tracked exemption with an issue number; otherwise extract `_decode_union`/`_decode_concrete` to a submodule.
