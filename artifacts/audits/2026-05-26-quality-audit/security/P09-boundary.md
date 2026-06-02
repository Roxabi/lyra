# Security Audit — Partition P09 (Boundary Layer)

**Date:** 2026-05-26
**Scope:** `src/lyra/integrations/**/*.py`, `src/lyra/tools/**/*.py`, `src/lyra/monitoring/**/*.py`, `src/lyra/obs/**/*.py`, `src/lyra/blobstore/**/*.py`
**Focus:** OWASP Top 10, credential handling, injection vectors, path traversal, sensitive data exposure, NATS ACL gaps

---

## Summary

- **3 Medium findings:** unbounded blob upload body (DoS), SSRF via unvalidated scraper URL, and health-check sensitive data leakage to external LLM.
- **4 Low findings:** unauthenticated metrics info-disclosure, weak health-secret validation, non-atomic monitoring state file, and deprecated supervisorctl missing input validation.
- **No critical/high findings.** No hardcoded secrets, no path-traversal escapes, and GH token dispenser follows defense-in-depth (rate floor, 0600 cache, 0660 socket, install-id regex guard).

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `src/lyra/blobstore/_handlers.py` | 105 | **Medium** | `handle_put` reads the entire request body via `await request.body()` with no Content-Length cap or max-size guard. A large upload can exhaust hub memory. | Reject requests where `Content-Length` exceeds a configured max (e.g., 50 MB) before reading the body, or enforce the limit via the ASGI server. |
| `src/lyra/integrations/web_intel.py` | 59 | **Medium** | `WebIntelScraper.scrape` passes the caller-supplied `url` directly to the external scraper subprocess without scheme validation, private-range block, or localhost denylist. SSRF risk against internal services or `file://` URLs if the downstream scraper resolves them. | Validate `url` scheme is `http` or `https`; reject URLs whose host resolves to private IP ranges, `localhost`, or link-local addresses before invoking the scraper. |
| `src/lyra/monitoring/escalation.py` | 73–103 | **Medium** | `_build_user_message` embeds all health-check `detail` fields (paths, container names, NATS monitor URL, auth-error counters) into the prompt sent to `claude -p` (external LLM API). Operational data is exposed to a third party without explicit opt-in or scrubbing. | Add a `scrub_for_llm()` step that strips sensitive fields (paths, URLs, tokens) from the prompt payload; gate LLM escalation behind an explicit `enable_llm_diagnosis` config flag. |
| `src/lyra/blobstore/serve.py` | 167, 174 | **Low** | `/healthz` and `/metrics` are allowlisted in `BearerAuthMiddleware` and therefore unauthenticated. They expose `disk_used_pct` and `blob_count`, which aid reconnaissance. | Either require bearer auth on these endpoints or reduce exposed fields; keep only a minimal `status: ok` on `/healthz` when unauthenticated. |
| `src/lyra/monitoring/config.py` | 40 | **Low** | `health_secret` accepts an empty string (disables auth) and has no minimum-length or complexity validation. A weak secret provides little protection for the health endpoint. | Enforce a minimum length (e.g., 16 characters) when `health_secret` is non-empty; log a warning if the secret is empty so operators know auth is off. |
| `src/lyra/monitoring/checks_varz.py` | 82 | **Low** | NATS `/varz` delta state is written with `open(state_path, "w")` — non-atomic and without permission checks. A symlink planted at `~/.lyra/nats-monitor-state.json` could redirect writes. | Write to a `.tmp` sibling, `chmod(0o600)`, then `os.replace()`; also check that the target is a regular file before overwriting. |
| `src/lyra/integrations/supervisor.py` | 63–65 | **Low** | Deprecated `SupervisorctlManager` passes raw `action` and `service` strings directly into `create_subprocess_exec` without validation. Although not a shell invocation, arbitrary arguments may be forwarded to the control script. | Accelerate removal of `SupervisorctlManager` (#1035); if retention is required, validate `action` against an allowlist and `service` against `_SERVICE_NAME_RE`. |

---

## Metrics

| Metric | Value |
|--------|-------|
| Files audited | 29 |
| Total findings | 7 |
| Critical | 0 |
| High | 0 |
| Medium | 3 |
| Low | 4 |
| Files with findings | 6 (21 %) |
| Hardcoded secrets/tokens | 0 |
| Path-traversal escapes | 0 |
| Injection vectors (validated) | 2 (1 SSRF, 1 subprocess arg) |

---

## Recommendations (Prioritized)

1. **Add a max body-size guard to `PUT /blobs`.** The blobstore is the only HTTP service in this partition that accepts arbitrary uploads; an unbounded read is the most exploitable weakness.
2. **Validate URLs before feeding `WebIntelScraper`.** A lightweight allowlist/blocklist prevents the scraper from becoming an SSRF proxy against the internal network.
3. **Scrub health-check payloads before LLM escalation.** The monitoring package currently forwards raw operational details to an external API; this is the partition’s largest sensitive-data-exposure surface.
4. **Harden unauthenticated blobstore endpoints.** Move `/healthz` and `/metrics` behind bearer auth, or at least drop `blob_count` and `disk_used_pct` from the unauthenticated response.
5. **Switch monitoring state writes to an atomic replace pattern.** Prevents symlink races and aligns with the same safe-write pattern already used by `TokenCache` in the GH token helper.
