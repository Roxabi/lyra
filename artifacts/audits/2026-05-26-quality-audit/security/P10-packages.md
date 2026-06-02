# Security Audit — Partition P10: packages/**/*.py (+ gh_token per focus area)

**Date:** 2026-05-26
**Scope:** roxabi-blobs, roxabi-contracts, roxabi-nats source + test doubles; `src/lyra/tools/gh_token/` covered per explicit focus-area request.
**Exclusions:** Hexagonal conformance, duplication, dead-code — those were covered by the 2026-05-18 audit and only re-reported if regressed.

---

### Summary

- **No Critical/High findings.** 9 issues: 5 Medium (NATS subject injection, unimplemented ADR-049 size gate, PEM permission bypass, reply-to exfil vector, dot-allowed inbox validator) and 4 Low (test-double trust-model deviation, HTTP client path escape, hints-cache memory pressure, GH token dispense audit gap).
- **Packages score well on OWASP injection & deserialization:** all SQL is parameterized, `json.loads` is the only deserialization path (no `pickle`/`yaml.load`/`eval`), and path-traversal guards exist for the FS blob store.
- **Two documentation/code drift items weaken the trust boundary:** ADR-049 claims a "pre-validation byte-size gate" in `roxabi_nats.deserialize()` that does not exist in code, and published testing doubles call `model_validate_json(msg.data)` directly despite the envelope docstring forbidding it.

---

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `packages/roxabi-contracts/src/roxabi_contracts/audit/blobs.py` | 64 | Medium | `BlobAuditEvent.subject_for(op)` interpolates `op` into a NATS subject without validation. An untrusted caller can inject extra segments or wildcards (e.g., `op="put.*"` → `lyra.audit.blobs.put.*`). | Validate `op` against the `Literal["put","get","exists","delete"]` set or apply `validate_job_token` before interpolation. |
| `packages/roxabi-nats/src/roxabi_nats/_validate.py` | 7 | Medium | `validate_nats_token` regex `[A-Za-z0-9_.\-]+` permits `.`, the NATS subject delimiter. An `identity_name` like `foo.bar` becomes inbox `_inbox.foo.bar`, broadening ACL match scope beyond a single segment. | Disallow dots in `validate_nats_token`; use `validate_worker_id` semantics for single-segment tokens. Retain dot-allowing variant only for full multi-segment subject validation. |
| `packages/roxabi-nats/src/roxabi_nats/_serialize.py` | 54-80 | Medium | `deserialize()` / `deserialize_dict()` lack any payload size limit, despite `ContractEnvelope` docstring (ADR-049 §Trust Model) claiming a "pre-validation byte-size gate" is enforced there. | Implement `MAX_PAYLOAD_BYTES` (e.g., 1 MiB aligned with NATS `max_payload`) and reject oversized `data` before `json.loads`. Update ADR-049 if the gate is intentionally removed. |
| `src/lyra/tools/gh_token/helper.py` | 86 | Medium | `JWTSigner` reads the GitHub App PEM private key without checking file permissions. A world-readable PEM allows any local user to mint installation tokens. | Add `O_NOFOLLOW` + `os.fstat` permission check (reject `mode & 0o077`) before reading, mirroring `roxabi_nats.connect._read_nkey_seed`. |
| `packages/roxabi-contracts/src/roxabi_contracts/jobs/models.py` | 37-45 | Medium | `JobEnvelope.reply_to` accepts any NATS subject with no prefix restriction. A compromised worker could set `reply_to` to a public outbound subject (e.g., `lyra.outbound.telegram.<id>`) and exfil job results. | Add a prefix allowlist validator for `reply_to` (e.g., `_INBOX.*` / `_R_.*`); keep override toggle for internal-only clusters. |
| `packages/roxabi-nats/src/roxabi_nats/testing/image.py` | 120 | Low | `FakeImageWorker._dispatch` calls `ImageRequest.model_validate_json(msg.data)` directly on raw NATS bytes, bypassing `roxabi_nats.deserialize()` and the (claimed) size gate. Same pattern in voice testing doubles. | Add a size gate before `model_validate_json`, or switch to `deserialize()` if the gate is implemented; document the deviation in ADR-049 if test doubles are exempt. |
| `packages/roxabi-nats/src/roxabi_nats/testing/voice.py` | 125, 236 | Low | `FakeTtsWorker._dispatch` and `FakeSttWorker._dispatch` call `model_validate_json(msg.data)` directly, identical violation to image testing double. | Apply same fix as image testing double; consolidate on a single helper. |
| `packages/roxabi-blobs/src/roxabi_blobs/http_store.py` | 122 | Low | `HttpBlobStore.get` interpolates `store_key` directly into the request path without URL-encoding. A traversal-style key (e.g., `../../admin/config`) escapes the `/blobs/` segment in the outgoing HTTP request. | Use `urllib.parse.quote(store_key, safe="")` before path interpolation. |
| `packages/roxabi-nats/src/roxabi_nats/_sanitize.py` | 68-73 | Low | `sanitize_platform_meta` assumes all dict keys are strings; a non-string key (`None`, `int`) raises `AttributeError` on `k.startswith("_")`. | Defensively skip or coerce non-string keys before applying the allowlist filter. |
| `packages/roxabi-nats/src/roxabi_nats/_serialize.py` | 34 | Low | `_hints_cache` is an unbounded `dict` with no eviction path. A long-running process deserializing many unique dataclass/resolver pairs can grow memory without limit. | Cap the cache with `functools.lru_cache` or an explicit `maxsize` bound. |
| `src/lyra/tools/gh_token/dispenser.py` | — | Low | No audit event is emitted on token dispensation. A leaked token cannot be traced to a specific request, app_id, or timestamp. | Emit a structured log line (or `SecurityEvent`-style record) on every successful dispense: timestamp, app_id hash, and client uid; mask the token value. |

---

### Metrics

| Metric | Value |
|---|---|
| Files analyzed (packages) | ~73 `.py` |
| Files analyzed (gh_token, per focus area) | 4 `.py` |
| Total findings | 11 |
| Medium | 5 (45%) |
| Low | 6 (55%) |
| High / Critical | 0 |
| OWASP Injection | 2 (NATS subject injection, HTTP path escape) |
| OWASP Security Misconfiguration | 2 (missing size gate, PEM permission bypass) |
| OWASP Broken Access Control | 1 (`reply_to` exfil vector) |
| OWASP Insufficient Logging | 1 (GH token dispense audit gap) |
| OWASP Insecure Deserialization | 0 (no `pickle`/`yaml.load`/`eval` found) |
| OWASP SSRF | 0 (no arbitrary URL fetch in packages) |
| OWASP Sensitive Data Exposure | 1 (PEM permission bypass) |
| OWASP XSS | 0 (no HTML rendering in packages) |
| OWASP Broken Authentication | 0 |
| OWASP XXE | 0 (no XML parsing in packages) |

---

### Recommendations (prioritized)

1. **Implement the missing ADR-049 payload size gate in `roxabi_nats.deserialize()`** (P0). The docstring promises a security control that does not exist; either add it or correct the documentation so downstream consumers do not rely on a phantom boundary.
2. **Harden NATS token validators to reject dots in single-segment identifiers** (P1). `validate_nats_token` is used for `inbox_prefix` and `identity_name`; a dot creates extra segments and can widen ACL scope. Introduce a dedicated `validate_nats_segment` that mirrors `validate_worker_id` (`[A-Za-z0-9_-]+`).
3. **Add PEM permission check in the GH token helper** (P1). The NATS nkey seed path already enforces owner-only mode; the GitHub App PEM should meet the same bar to prevent token minting by unauthorized local users.
4. **Restrict `JobEnvelope.reply_to` to inbox prefixes** (P2). A compromised worker should not be able to publish job results to arbitrary NATS subjects. A prefix allowlist (`_INBOX.*`, `_R_.*`) closes the lateral-exfil path without breaking legitimate request/reply patterns.
5. **Backfill test-double compliance with the ADR-049 trust model** (P3). Published testing doubles (`FakeImageWorker`, `FakeTtsWorker`, `FakeSttWorker`) are the de-facto reference implementations for worker authors. Adding a size gate and switching to `deserialize()` (or documenting the deviation) prevents copy-paste propagation of unsafe patterns.
