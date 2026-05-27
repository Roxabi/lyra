# Tech-Debt Audit — P10: packages/**/*.py

**Scope:** roxabi-contracts, roxabi-nats, roxabi-blobs
**Date:** 2026-05-26
**Total:** 113 `.py` files / 15,842 lines (65 source, 48 test)

---

## Summary

- **Quality-gate blind spot:** 2 source files >300 lines and 1 folder >12 files in packages have **zero** exemptions in `tools/file_exemptions.txt` or `tools/folder_exemptions.txt`. Enforcement may silently skip packages, or exemptions were never added.
- **ADR-059 V6 shims are the dominant debt:** 5 backward-compat re-export shims span contracts/nats boundaries. All are intentional, deprecation-warned, and tracked — but they still inflate import paths and require maintenance until removal at `roxabi-nats v0.3.0`.
- **Otherwise clean:** Only 1 TODO (testing code), zero deprecated `asyncio` APIs, no abstract debt buckets, and no ADR-048 store/ports drift (packages are transport/schema, not application-layer stores).

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `packages/roxabi-blobs/src/roxabi_blobs/fs_store.py` | 1 | **Medium** | 344 lines — exceeds 300-line gate; **not exempted** in `tools/file_exemptions.txt` | Either add exemption with issue ref or extract `_write_blob_file` / `_lookup_existing` to a storage-ops module |
| `packages/roxabi-nats/src/roxabi_nats/_serialize.py` | 1 | **Medium** | 369 lines — exceeds 300-line gate; **not exempted**. Was already split once (`_resolver.py` extracted to stay under cap) and has regrown | Extract `_encode_pydantic` / `_encode` / `_decode` family into `_serialize_codec.py`; cap `_serialize.py` to orchestration only |
| `packages/roxabi-nats/src/roxabi_nats/` | — | **Medium** | 13 `.py` files at root — exceeds 12-file folder gate; **not exempted** in `tools/folder_exemptions.txt` | Either add exemption (e.g. `#1278 codec files`) or split `_serialize.py` + `_resolver.py` into a `roxabi_nats/codec/` sub-package |
| `packages/roxabi-contracts/src/roxabi_contracts/_testing_guards.py` | 1 | **Low** | Deprecated shim re-exporting `ALLOWED_LOOPBACK_HOSTS`, `assert_loopback_url`, `assert_not_production` from `roxabi_nats.testing._guards` (ADR-059 V6) | Schedule removal; verify zero internal callers remain via `grep -r "roxabi_contracts._testing_guards" src/` |
| `packages/roxabi-nats/src/roxabi_nats/_tts_constants.py` | 1 | **Low** | Deprecated shim re-exporting `AGENT_TTS_FIELDS` / `TTS_CONFIG_FIELDS` from `roxabi_contracts.voice.constants` with `noqa: F401` + `pyright: ignore` | Schedule removal at v0.3.0; grep for `_tts_constants` imports |
| `packages/roxabi-contracts/src/roxabi_contracts/voice/testing.py` | 1 | **Low** | Deprecated shim re-exporting `FakeTtsWorker` / `FakeSttWorker` from `roxabi_nats.testing.voice` (ADR-059 V6) | Schedule removal; grep for `roxabi_contracts.voice.testing` imports |
| `packages/roxabi-contracts/src/roxabi_contracts/image/testing.py` | 1 | **Low** | Deprecated shim re-exporting `FakeImageWorker` from `roxabi_nats.testing.image` (ADR-059 V6) | Schedule removal; grep for `roxabi_contracts.image.testing` imports |
| `packages/roxabi-nats/src/roxabi_nats/adapter_base.py` | 57 | **Low** | `CONTRACT_VERSION` lazy `__getattr__` shim emits `DeprecationWarning`; marked "Remove at roxabi-nats v0.3.0" | Set calendar reminder tied to v0.3.0 tag; remove shim + `__getattr__` boilerplate |
| `packages/roxabi-nats/src/roxabi_nats/driver_base.py` | 169 | **Low** | `asyncio.Queue(maxsize=512)` — magic number without named constant | Extract `DEFAULT_INBOX_QUEUE_SIZE = 512` class constant |
| `packages/roxabi-nats/src/roxabi_nats/testing/voice.py` | 55,75,168,188 | **Low** | `nats://127.0.0.1:4222` repeated 4× in `FakeTtsWorker`/`FakeSttWorker` defaults | Extract `DEFAULT_TEST_NATS_URL: str = "nats://127.0.0.1:4222"` in `testing/_constants.py` |
| `packages/roxabi-nats/src/roxabi_nats/testing/image.py` | 56,76 | **Low** | `nats://127.0.0.1:4222` repeated 2× in `FakeImageWorker` defaults | Reuse same constant as above |
| `packages/roxabi-blobs/src/roxabi_blobs/http_store.py` | 89 | **Low** | `httpx.Timeout(5.0, connect=5.0)` hardcoded per-request timeout | Extract `DEFAULT_TIMEOUT = httpx.Timeout(5.0, connect=5.0)` class constant |
| `packages/roxabi-blobs/src/roxabi_blobs/http_store.py` | 75 | **Low** | `asyncio.wait_for(..., timeout=2.0)` hardcoded ASGI lifespan shutdown timeout | Extract `LIFESPAN_SHUTDOWN_TIMEOUT_S = 2.0` class constant |
| `packages/roxabi-nats/src/roxabi_nats/readiness.py` | 160,162 | **Low** | `0.5` second sleep/retry interval hardcoded in `_open_kv_with_retry` | Extract `KV_RETRY_INTERVAL_S = 0.5` module constant |
| `packages/roxabi-nats/src/roxabi_nats/testing/voice.py` | 113 | **Low** | `# TODO(#761 follow-up): add explicit drain-timeout test when the first...` | Resolve if #761 is closed; otherwise no action |
| `packages/roxabi-nats/src/roxabi_nats/readiness.py` | 51,56,61,157 | **Low** | 4× `type: ignore[union-attr]` on `js.key_value()` / `js.create_key_value()` — pyright cannot resolve nats.js KV types | If nats-py stubs improve, remove ignores; otherwise document as external-stub debt |

---

## Metrics

| Metric | Count | Notes |
|--------|-------|-------|
| Total `.py` files | 113 | 65 source + 48 test |
| Total lines | 15,842 | contracts 2,078 / nats 2,385 / blobs 833 (source only) |
| TODO / FIXME / HACK / XXX | **1** | Single TODO in `testing/voice.py` (#761 follow-up) |
| Deprecated stdlib API usage | **0** | No `asyncio.coroutine`, `get_event_loop`, etc. |
| Deprecation shims (ADR-059 V6) | **5** | 2 in contracts, 3 in nats — all warned + scheduled |
| Files >300 lines (source) | **2** | `fs_store.py` (344), `_serialize.py` (369) |
| Exemptions for packages | **0** | Neither `file_exemptions.txt` nor `folder_exemptions.txt` lists any `packages/` path |
| Folders >12 files (source root) | **1** | `roxabi_nats/` root = 13 `.py` files |
| `noqa` / `type: ignore` in source | ~22 | Mostly E501 (ADR paths, error descriptions) + PLR0913 (locked signatures per ADR-067/#1333) |
| `noqa` / `type: ignore` in tests | 54 | Broader suppression tolerance acceptable in tests |
| Magic numbers / strings (source) | ~8 | See Findings table for locations |
| ADR-048 store/ports drift | **N/A** | Packages are transport/schema; no store implementations |
| Abstract debt buckets | **0** | No "async-pipeline", "P2b", or untracked placeholders |

---

## Recommendations (prioritized)

1. **Close the quality-gate blind spot** — Verify whether `tools/check_*.sh` / pre-commit actually scans `packages/**/*.py`. If yes, add exemptions for `fs_store.py`, `_serialize.py`, and `roxabi_nats/` root with issue refs (or refactor to shrink them). If no, extend gate globs to cover packages explicitly.
2. **Calendar the ADR-059 V6 shim removals** — All 5 shims reference "v0.3.0". When that version is tagged, delete `_testing_guards.py`, `_tts_constants.py`, `voice/testing.py`, `image/testing.py`, and the `adapter_base.__getattr__` shim in a single cleanup PR.
3. **Extract testing-double NATS URL constant** — Replace 6 hardcoded `nats://127.0.0.1:4222` defaults across `testing/voice.py` and `testing/image.py` with one constant in `testing/_constants.py`.
4. **Name the driver_base queue size** — `maxsize=512` in `NatsDriverBase._dict_stream_gen` should be `DEFAULT_INBOX_QUEUE_SIZE = 512` for discoverability and testability.
5. **Split `_serialize.py` (369 lines)** — It already spawned `_resolver.py` to stay under cap. Extract the `_encode*` / `_decode*` codec functions into a new `_serialize_codec.py` module so `_serialize.py` remains the public orchestration layer under 300 lines.
