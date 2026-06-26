# Security Audit — P01: src/lyra/core/hub

**Date:** 2026-05-26
**Scope:** `src/lyra/core/hub/**/*.py` (+ related transport, gh_token, deploy/nats context for focus-area completeness)
**Prior audit baseline:** 2026-05-18 (hexagonal/mutualization/dead-code). No regressions observed on those axes.

---

## Summary

- **1 medium-severity finding:** NATS subject injection via unsanitized string interpolation in `TypingPublisher` (consumed by hub bootstrap) enables wildcard-driven subject expansion.
- **4 low-severity findings:** PII exposure in structured audit logs, broad exception masking in session resume, log injection from unsanitized user text, and pseudo-JSON log corruption in outbound dispatch.
- **GH token dispenser (`tools/gh_token/`)** shows mature guards (numeric install_id validation, atomic 0600 cache, 0660 socket with group isolation, 15-min TTL floor, abuse-rate limiter) — no critical gaps.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `src/lyra/transport/typing_publisher.py` | 52 | **Medium** | NATS subject built via f-string: `f"lyra.typing.{event.scope.platform}.{event.scope.bot_id}"`. If `platform` or `bot_id` contain NATS wildcards (`*`, `>`) or dots, the hub publishes to unintended subject hierarchies, bypassing ACL granularity. | Sanitize each segment before interpolation: strip `*` and `>`, validate against an allowlist, or replace dots with a safe delimiter. |
| `src/lyra/core/hub/pipeline/audit_consumer.py` | 37 | **Low** | `dataclasses.asdict(event)` logs `user_id`, `scope_id`, and `platform` at INFO level as structured JSON. This is persistent PII in logs with no redaction or access-classification. | Redact or hash `user_id`/`scope_id` in audit events, or move the consumer to a dedicated trace-level logger with restricted retention. |
| `src/lyra/core/hub/middleware/middleware_submit.py` | 92 | **Low** | `resolve_context` catches `Exception` broadly (`BLE001`) and silently degrades to `ResumeStatus.SKIPPED`. Auth failures, store tampering, or integrity errors are swallowed without escalating. | Catch specific expected exceptions (store timeout, not-found) only; let unexpected exceptions propagate to trigger monitoring alerts. |
| `src/lyra/core/hub/middleware/middleware_pool.py` | 168-170 | **Low** | `_cmd = msg.text.split()[0]` is extracted from raw user text and passed unmodified to `log.info`. Newlines or format specifiers can corrupt log lines or confuse downstream parsers. | Strip control characters from `_cmd` before logging, or use `%r` (repr) formatting to contain the payload. |
| `src/lyra/core/hub/outbound/_dispatch.py` | 83-85 | **Low** | JSON-structured log message uses `%s` placeholders with raw `platform_name` and `kind` values. Unescaped quotes or braces in these strings break structured log parsing downstream. | Build the log record with `json.dumps({"event": ..., "action": ..., "dropped": True})` instead of format-string pseudo-JSON. |
| `deploy/nats/acl-matrix.json` (hub section) | — | **Low** | Hub, adapters, and workers are granted blanket `$JS.API.>` and `$KV.lyra-state.>` permissions. Notes acknowledge this as follow-up #1293 but it remains active in production auth.conf. | Tighten grants to per-stream/per-key subjects (e.g., `$JS.API.STREAM.INFO.LYRA_TURNS`) and minimize `$KV.lyra-state.>` scope per role. |

---

## Metrics

| Metric | Value |
|--------|-------|
| Files scanned (hub partition) | 27 |
| Related files reviewed (transport / gh_token / deploy) | 9 |
| **Critical** findings | 0 |
| **High** findings | 0 |
| **Medium** findings | 1 |
| **Low** findings | 5 |
| OWASP Injection (A03) | 2 (NATS subject, log content) |
| OWASP Sensitive Data Exposure (A01) | 1 (audit logs) |
| OWASP Security Misconfiguration (A05) | 1 (NATS ACL over-permission) |
| OWASP Insufficient Logging / Monitoring (A09) | 1 (broad exception swallowing) |
| Broken Access Control (A01) | 0 in partition code |
| XSS / XXE / Insecure Deserialization / SSRF | 0 in partition code |
| GH token dispenser gaps | 0 |

---

## Recommendations (prioritized)

1. **Sanitize NATS subject segments in `TypingPublisher`** — Strip or reject `*`, `>`, and unescaped dots before building `lyra.typing.{platform}.{bot_id}`. This is the only medium-severity vector and directly impacts ACL integrity.
2. **Add targeted exception handling in `SubmitToPoolMiddleware`** — Replace the `except Exception` boundary around `resolve_context` with specific catch clauses for known transient failures. Unexpected exceptions should bubble up to the hub loop so monitoring can alert on them.
3. **Redact PII in `AuditConsumer` structured logs** — Hash or mask `user_id` and `scope_id` before emitting. If full fidelity is required for debug, gate it behind a `TRACE` level that is not shipped to persistent log storage.
4. **Harden NATS ACLs per tracked issue #1293** — Move from blanket `$JS.API.>` and `$KV.lyra-state.>` to per-operation subjects in `acl-matrix.json` / `auth.conf`. Validate the tightened matrix with a CI gate that diffs rendered auth.conf against the matrix.
5. **Sanitize user-derived values before logging** — Apply a small utility (e.g., strip control chars, limit length) to `_cmd`, `platform_name`, and any other user-originated strings before they enter `log.info` / `log.warning` calls.
