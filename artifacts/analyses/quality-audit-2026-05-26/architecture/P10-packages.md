# Architecture Audit — Partition P10: packages/**/*.py

**Date:** 2026-05-26
**Scope:** roxabi-blobs, roxabi-contracts, roxabi-nats (source only, tests excluded)
**Context:** First full architecture review of packages (explicitly left unaudited by 2026-05-18 audit). Focus on new/changed code since 2026-05-18.

---

## Summary

- **Cross-package dependency inversion:** `roxabi-contracts` (schema layer) imports from `roxabi-nats` (transport SDK) via 3 backward-compat shims, creating a bidirectional package cycle and violating the intended bottom-up layering.
- **BlobRef sentinel models diverged post-#1367:** `roxabi-blobs` adopted `is_sentinel=True` while `roxabi-contracts` retains `store_key == PENDING_STORE_KEY`; the two sentinel designs are not mutually compatible and explicit warnings guard the boundary.
- **Packages are absent from importlinter:** No layer or independence contracts enforce inter-package or intra-package boundaries; the contracts→nats cycle and any future drift are invisible to CI.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `packages/roxabi-contracts/src/roxabi_contracts/_testing_guards.py` | 8 | **High** | Deprecated shim imports from `roxabi_nats.testing._guards`, making the schema package depend on the transport SDK. | Remove shim; migrate remaining consumers to `roxabi_nats.testing._guards`. |
| `packages/roxabi-contracts/src/roxabi_contracts/voice/testing.py` | 8 | **High** | Deprecated shim imports `FakeSttWorker`/`FakeTtsWorker` from `roxabi_nats.testing.voice`. Creates a runtime path from contracts → nats → contracts. | Remove shim; migrate consumers to `roxabi_nats.testing.voice`. |
| `packages/roxabi-contracts/src/roxabi_contracts/image/testing.py` | 8 | **High** | Deprecated shim imports `FakeImageWorker` from `roxabi_nats.testing.image`. Same cycle risk as voice.testing. | Remove shim; migrate consumers to `roxabi_nats.testing.image`. |
| `packages/roxabi-blobs/src/roxabi_blobs/http_store.py` | 158 | **Medium** | `HttpBlobStore.exists()` returns a sentinel `BlobRef` with `is_sentinel=True` and `content_hash=""`, but `roxabi_contracts.BlobRef` validator rejects `content_hash=""` unless `store_key == PENDING_STORE_KEY`. Explicit code comment warns against forwarding this sentinel into the wire model. | Converge on a single sentinel representation across packages (either `is_sentinel` field in contracts, or `PENDING_STORE_KEY` in blobs), or provide a bounded conversion helper. |
| `packages/roxabi-nats/src/roxabi_nats/__init__.py` | 14 | **Low** | Re-exports `CONTRACT_VERSION` from `roxabi_contracts` eagerly without deprecation warning. Inconsistent with `adapter_base.py` where accessing the same name emits `DeprecationWarning` per ADR-059 V4. | Add `__getattr__`-based lazy deprecation to `__init__.py`, or remove the re-export and direct consumers to `roxabi_contracts.envelope`. |
| `packages/roxabi-nats/src/roxabi_nats/testing/__init__.py` | 5 | **Low** | Docstring references `roxabi_contracts.{voice,image}.testing` as origin per ADR-059 V6, but the canonical location is now `roxabi_nats.testing.*`. | Update docstring to reflect current canonical ownership and remove stale origin reference. |
| `.importlinter` | — | **Medium** | No contracts cover `roxabi_contracts`, `roxabi_nats`, or `roxabi_blobs`. The contracts→nats cycle and any future intra-package layer violations are not gated in CI. | Add an `importlinter:contract:packages-layer` section enforcing `roxabi_contracts` ← `roxabi_nats` ← `roxabi_blobs` (or appropriate DAG) with explicit exemptions for intentional test-only edges. |
| `packages/roxabi-contracts/README.md` | ~146 | **Low** | Documentation still claims `roxabi_contracts.voice.testing` provides test doubles (ADR-059 V6 moved them to `roxabi_nats.testing`). | Update README to reference `roxabi_nats[testing]` and remove stale claims. |

---

## Metrics

| Metric | Value |
|--------|-------|
| Total source `.py` files audited | 45 |
| Cross-package import edges | 12 (nats → contracts: 10; contracts → nats: 3 shims) |
| Package-to-lyra imports (source) | 0 |
| Intra-package circular dependencies | 0 |
| Files with `aiosqlite` / `sqlite3` imports (ADR-048 scope) | 1 (`fs_store.py`) — package-internal, protocol is clean |
| roxabi-contracts domain modules | 8 (voice, image, llm, turns, jobs, gh, cli, audit) — no cross-domain coupling beyond shared envelope/errors/blob_ref |
| roxabi-nats internal modules | 12 — adapter/driver split is clean, all files are transport-scoped |
| roxabi-blobs internal modules | 8 — Protocol + 2 implementations + models + errors + schema + ingest — hexagonal shape is correct |
| Findings since 2026-05-18 | 8 (3 high, 3 medium, 2 low) |
| High-severity findings | 3 (all are the contracts→nats shim cycle) |

---

## Recommendations (prioritized)

1. **Remove the 3 contracts→nats backward-compat shims (#ADR-059 V6 close-out)**
   The shims (`_testing_guards.py`, `voice/testing.py`, `image/testing.py`) have been deprecated since ADR-059 V6. They are the only reason the schema package depends on the transport SDK. Deleting them breaks the bidirectional cycle and restores the intended bottom-up layering. Update `README.md` and any lingering consumer imports before removal.

2. **Converge BlobRef sentinel representation across roxabi-blobs and roxabi-contracts**
   The post-#1367 `is_sentinel` field in `roxabi_blobs.models.BlobRef` does not align with the `PENDING_STORE_KEY` sentinel in `roxabi_contracts.blob_ref.BlobRef`. Pick one mechanism (recommend `is_sentinel` field because it is machine-checkable and works for HTTP HEAD-only paths where `store_key` is a real path, not a sentinel string), propagate it to `roxabi_contracts`, and remove the manual conversion warnings in `HttpBlobStore`.

3. **Add package-level importlinter contract**
   Introduce a dedicated `.importlinter` section for the three packages. Enforce: `roxabi_contracts` ← `roxabi_nats` ← `roxabi_blobs` (or the actual DAG after shim removal). This gates the cycle at CI time and prevents future cross-package drift as the workspace grows.

4. **Align `CONTRACT_VERSION` deprecation in `roxabi_nats.__init__.py`**
   Either add a `__getattr__` lazy deprecation to match `adapter_base.py`, or remove the re-export entirely and direct consumers to `roxabi_contracts.envelope.CONTRACT_VERSION`. The inconsistency confuses consumers about which import path is canonical.

5. **(Positive) roxabi-blobs hexagonal shape is exemplary — preserve it**
   `BlobStore` Protocol + `FsBlobStore`/`HttpBlobStore` implementations + `BlobRef` model + `ingest.py` service helper is a clean port/adapter/model separation. If a future S3/MinIO implementation is added, keep it behind the same Protocol and do not let S3 SDK imports leak into `models.py` or `protocol.py`.
