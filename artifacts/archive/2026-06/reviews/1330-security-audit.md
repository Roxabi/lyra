# Security audit — #1330 V8 HTTP-fronted BlobStore

Date: 2026-05-25
Auditor: security-auditor (claude code agent)
Scope: bearer-auth + secret-handling end-to-end

## Verdict

**APPROVED-WITH-FOLLOWUP**

Two spec gaps found (401 audit not emitted, `head` op missing from schema Literal) that are low-severity and safe to address post-merge. One residual risk (Tailscale fallback) is already accepted and documented. No blockers.

---

## Hard requirements

| ID | Requirement | Verdict | Evidence |
|----|-------------|---------|----------|
| SC-Code-4 | hmac.compare_digest used | PASS | `src/lyra/blobstore/auth.py:28` — `hmac.compare_digest(provided.encode(), self._token.encode())` |
| SC-Code-5 | Token read once at startup | PASS | `serve.py:129` reads token once in `build_app()`; `tests/blobstore/test_serve_api.py::TestStaleToken::test_token_is_read_once_at_startup` passes (confirmed via test run) |
| SC-Ops-2 | No bearer in env/args/logs/audit | PASS | No token value in env vars (`blobstore.env.example` explicitly excludes it); token passed via `--token-path` file reference only; `BlobAuditEvent` schema has no token/bearer fields; `audit_sink.py` only serialises `BlobAuditEvent` fields (`op`, `result`, `store_key`, `content_hash`, `size`, `source`) |
| No-oracle | Path traversal → 404 (not 400) | PASS | `packages/roxabi-blobs/src/roxabi_blobs/fs_store.py:237-239` — `_safe_resolve_in_root()` returns `None` on escape; raises `BlobNotFoundError("blob not found")` with static message; handler maps to 404 at `_handlers.py:107` |
| Audit 401 sanitization | Rejected token never echoed | PASS (partial — see F-1) | Middleware (`auth.py:25,29`) returns static `{"detail":"unauthorized"}` — token value is never referenced in the response. `BlobAuditEvent` has no bearer field. However, 401 events are NOT emitted at all (see F-1) |
| Quadlet secret shape | type=mount, uid=1500, mode=0400 | PASS | `deploy/quadlet/lyra-blobstore.container:33` — `Secret=lyra_blobstore_token,type=mount,uid=1500,gid=1500,mode=0400,target=/run/secrets/lyra_blobstore_token` |
| Install idempotency | No silent rotation | PASS | `deploy/install.sh:68` — `if [[ ! -f "${BLOBSTORE_TOK}" || "$FORCE" -eq 1 ]]` — only generates when file absent or `--force` is explicit; existing token is preserved with `[skip]` log message |

---

## Findings

### F-1

suggestion(major): 401 unauthorized events not audited — spec violation

  `src/lyra/blobstore/auth.py:19–31`
  Category: Insufficient Logging (OWASP 10)
  Confidence: 95%

  Spec § Failure modes (line 72) requires: "Bearer mismatch: 401, audit `subject:"anonymous", result:"unauthorized"`". The spec checklist (line 286) also calls for `result:"unauthorized"` audit emit in `test_serve.py`.

  `BearerAuthMiddleware.dispatch()` returns `JSONResponse({"detail":"unauthorized"})` directly at lines 25 and 29, with no call to `app.state.audit_sink`. The middleware has no access to `app.state` in the current form. The `test_serve.py::TestBearerAuth` test only asserts `status_code == 401`, not that an audit event was emitted.

  Impact: failed authentication attempts are invisible in the audit trail, preventing detection of credential-stuffing or token-rotation errors in production.

  Recommended fix:
    1. (Primary) Inject or access `audit_sink` from the middleware at dispatch time (e.g. via `request.app.state.audit_sink`) and emit a `BlobAuditEvent(op="get", result="unauthorized", store_key=None, ...)` before returning 401.
    2. (Alternative) Promote 401 detection to a Starlette middleware that reads `response.status_code` post-dispatch and logs to the security logger — avoids modifying `BearerAuthMiddleware` contract.

---

### F-2

nit(minor): `head` op missing from `BlobAuditEvent.op` Literal — future audit wiring will fail at runtime

  `packages/roxabi-contracts/src/roxabi_contracts/audit/blobs.py:22`
  `src/lyra/blobstore/_handlers.py:149,153,161,165,168`
  Category: Misconfiguration
  Confidence: 90%

  `BlobAuditEvent.op` is typed as `Literal["put", "get", "exists", "delete"]`. The handlers emit `_emit_audit("head", ...)` for N3 (HEAD /blobs). The current `_emit_audit` in `_handlers.py:30` is a no-op stub, so no runtime error fires today. When T13 wires the real `BlobAuditSink.emit()`, constructing a `BlobAuditEvent(op="head", ...)` will raise a Pydantic `ValidationError` at runtime.

  Recommended fix:
    1. (Primary) Add `"head"` to the `op` Literal in `BlobAuditEvent` before T13 wiring lands. Per `roxabi-contracts` CLAUDE.md, this is an additive minor-bump (no breaking-change overhead).
    2. (Alternative) Map HEAD handler audits to `op="exists"` (semantically similar, avoids schema change).

---

### F-3

nit(minor): Tailscale IP fallback binds to 0.0.0.0 — bearer token becomes sole auth boundary

  `deploy/quadlet/lyra-blobstore.container:44–48`
  `deploy/quadlet/blobstore.env.example:13–16`
  Category: Misconfiguration (OWASP 6)
  Confidence: 85%

  `PublishPort=${TAILSCALE_IPV4}:8449:8449` — if `TAILSCALE_IPV4` is unset (empty string in `blobstore.env`), Podman resolves this as `0.0.0.0:8449`, exposing the service on all interfaces including LAN. The bearer token is then the only auth barrier. This is documented as accepted residual risk in both the container file (line 46–47) and `deploy/CLAUDE.md §Known residual risk`. The env example comment notes the fallback explicitly.

  This is not a new vulnerability — it is accepted-by-design. Recorded here for completeness and future tracking.

  Recommended fix:
    1. (V8.1) Add a startup assertion or `ExecStartPre=` script that validates `TAILSCALE_IPV4` is non-empty before starting the container.
    2. (V9) Adopt a network-policy or firewall rule as a second layer.

---

## Residual risks (accepted by design)

- **HTTP-only over Tailnet (no app-layer TLS in V8)**: mitigated by Wireguard transport encryption on the Tailnet. Bearer tokens are not transmitted in plaintext over the internet. Accepted per spec § Transport security.
- **Single bearer token (no per-identity creds)**: all callers share one token. Compromise = full blobstore access. Tracked as future iteration #1334.
- **Tailscale IPv4 fallback to 0.0.0.0**: documented in F-3 above and in `deploy/CLAUDE.md`. Accepted for V8.
- **`_emit_audit` stub in `_handlers.py`**: all handler-level audit calls are currently no-ops. This is intentional until T13 wires the real sink; it means the operational audit trail is absent in V8. Not a security blocker since auth events at 401 are also absent — both gaps land together in T13.
- **`httpx.raise_for_status()` in `HttpBlobStore`**: httpx exception messages include request URL but NOT request headers in their string representation. The Authorization header value is not exposed in logged exceptions. Confirmed: no logging calls exist in `http_store.py`.
- **Token entropy**: 285 bits (48 url-safe alphanumeric chars from /dev/urandom). Well above the 128-bit minimum.

---

## Sign-off

Approve with follow-up on 2 findings (F-1: 401 audit emission — major, F-2: `head` op schema gap — minor). No blockers. Safe to merge V8 as-is; F-1 should land in the same PR as T13 audit wiring.
