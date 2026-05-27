### Summary
- Partition P07 (infrastructure, transport, NATS, gh_token) is structurally sound: all SQL is parameterized, no eval/exec/pickle, GH tokens use lazy 15-min TTL refresh with 0600 atomic cache writes, and NATS wildcard injection is blocked at the primary validation layer (`validate_nats_token`, `WorkScope` regex).
- Three defense-in-depth gaps remain: a public queue-group helper that omits NATS token sanitization, an unused sanitizer in `nats_channel_proxy.py`, and a degraded-mode audit sink that falls back to a local logger with full event JSON.
- No hardcoded secrets, no command injection, no SSRF vectors, and no broken-auth findings.

### Findings
| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `src/lyra/nats/queue_groups.py` | 30 | **Medium** | `adapter_outbound(platform, bot_id)` interpolates raw strings into a NATS queue group name without sanitizing `*`, `>`, `.` or spaces. Current callers pass validated enum values and bot IDs, but the public API lacks defense-in-depth against future misuse. | Sanitize both arguments with `validate_nats_token` (or `_safe_subject_token`) before returning the f-string. |
| `src/lyra/nats/nats_channel_proxy.py` | 43-45 | **Low** | `_safe_subject_token` is defined but never invoked; the class relies solely on `__init__` regex validation of `bot_id`. If state were ever mutated or a new method bypassed validation, no secondary guard exists. | Either wire `_safe_subject_token` into every subject-token construction site, or remove the dead code to avoid a false sense of defense-in-depth. |
| `src/lyra/infrastructure/audit/jetstream_sink.py` | 93-98 | **Low** | When JetStream is degraded/unavailable, the full `SecurityEvent` JSON (pool_id, agent_name, tools_allowlist, model, pid, skip_permissions flag) is written to the `lyra.security` logger. Log backends may have weaker ACLs than the NATS audit stream. | Redact or hash identifying fields before writing to the fallback logger, or document the degraded-path data classification in the deployment runbook. |
| `src/lyra/tools/gh_token/daemon.py` | 88-90 | **Low** | `LYRA_GH_PEM_PATH` is validated with `is_file()` but not restricted to an expected directory (e.g., `/run/lyra-gh-token/` or `/secrets/`). A compromised environment could point the helper at any readable PEM on the host. | Enforce a path prefix whitelist and reject paths outside it at startup. |
| `src/lyra/infrastructure/turn_writer/writer.py` | 129 | **Low** | `TurnWriteEvent.model_validate_json(msg.data)` deserializes NATS payloads without an explicit size guard. The NATS server MaxPayload limit is the only size boundary; local or misconfigured servers could allow oversized messages. | Add a `len(msg.data)` pre-check (e.g., reject > 1 MB) before Pydantic deserialization to fail fast independent of broker config. |
| `src/lyra/nats/nats_bus.py` | 234 | **Low** | `json.loads(msg.data.decode("utf-8"))` on inbound NATS messages has no explicit payload size check before deserialization. Same rationale as turn_writer. | Mirror the size guard used for turn_writer here (reject > 1 MB). |

### Metrics
- Files scanned: 41 Python files (~6,470 lines)
- Findings: 6 (1 Medium, 5 Low)
- Injection vectors: 0 exploitable (all SQL parameterized; 1 f-string SQL in `prefs_store.py:94` uses only `?` placeholders, no user content in the query string)
- Hardcoded secrets: 0
- eval / exec / pickle / shell=True: 0
- OWASP Top 10 coverage:
  - A01 Broken Access Control: 1 finding (queue group wildcard gap)
  - A02 Cryptographic Failures: 0
  - A03 Injection: 0 exploitable
  - A04 Insecure Design: 1 finding (unused sanitizer)
  - A05 Security Misconfiguration: 2 findings (PEM path unrestricted, payload size unguarded)
  - A06 Vulnerable Components: 0
  - A07 Auth Failures: 0
  - A08 Data Integrity Failures: 0
  - A09 Security Logging Failures: 1 finding (degraded audit fallback)
  - A10 SSRF: 0

### Recommendations (prioritized)
1. **Sanitize `adapter_outbound` inputs** — Apply `validate_nats_token` to `platform` and `bot_id` inside `queue_groups.py` to close the wildcard-injection gap for any future caller.
2. **Add payload size pre-checks** — Insert `len(msg.data)` guards before `json.loads`/`model_validate_json` in `NatsBus._handle_nats_message` and `TurnWriter._consume_loop` (e.g., 1 MB ceiling) to bound memory regardless of NATS MaxPayload config.
3. **Harden degraded audit logging** — In `JetStreamAuditSink.emit`, redact or hash `pool_id`/`agent_name` when writing to the fallback `lyra.security` logger so degraded mode does not expand the data-exposure surface.
4. **Whitelist PEM directory** — Restrict `LYRA_GH_PEM_PATH` to a known secrets directory in `daemon.py` `_load_config`.
5. **Remove or wire `_safe_subject_token`** — Either use it in all subject construction inside `NatsChannelProxy` or delete the dead code.
