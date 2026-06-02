# Security Audit — Partition P06 (bootstrap)
**Date:** 2026-05-26
**Scope:** `src/lyra/bootstrap/**/*.py` + contextual files (`tools/gh_token/`, `deploy/nats/`)
**Auditor:** Claude Code
**Prior audit (2026-05-18):** Hexagonal conformance, duplication, dead-code — excluded unless regressed.

---

### Summary
- **4 medium path-traversal gaps** from unvalidated env-var-derived paths (`LYRA_VAULT_DIR`, `LYRA_AGENT_STORE_PATH`, `LYRA_TURNS_DB`, `LYRA_RUN_SECRETS_DIR` in non-prod). `config.py`’s trusted-base helper is itself bypassable via `$HOME` manipulation.
- **NATS transport exposure:** container config binds `0.0.0.0` without TLS; local dev config disables auth entirely. Broad `$JS.API.>` grants in `auth.conf` violate least-privilege (tracked in #1293).
- **No hardcoded secrets or credential leaks.** GH-token helper architecture is sound, but has a Unix-socket permission race and an unvalidated PEM path.

---

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|--------------|
| `src/lyra/bootstrap/infra/lockfile.py` | 17 | **Medium** | `LYRA_VAULT_DIR` used without path validation; an attacker controlling the env can cause the lockfile to be written/read anywhere. | Apply `_validate_config_path` semantics or reject `..` and absolute-path components. |
| `src/lyra/bootstrap/factory/agent_store_factory.py` | 35 | **Medium** | `LYRA_AGENT_STORE_PATH` used without validation as the `JsonAgentStore` file path. | Validate the resolved path is within the trusted home directory. |
| `src/lyra/bootstrap/standalone/turn_writer_standalone.py` | 47 | **Medium** | `LYRA_TURNS_DB` used without validation for the SQLite database path. | Validate with the same trusted-base helper before opening. |
| `src/lyra/bootstrap/credentials.py` | 37 | **Medium** | `LYRA_RUN_SECRETS_DIR` override is not validated in non-prod environments; `bot_id` from TOML config is not sanitized before file-path interpolation. | Validate override path; restrict `bot_id` to `[A-Za-z0-9_-]` to prevent path traversal. |
| `src/lyra/bootstrap/factory/config.py` | 128 | **Medium** | `_validate_config_path` uses `Path.home()` which respects `$HOME`, making the trusted-base check self-referential and bypassable by an attacker who can set `$HOME`. | Resolve the trusted base via `pwd.getpwuid(os.getuid()).pw_dir` or `os.path.expanduser('~')` with a hardcoded fallback. |
| `deploy/nats/nats-container.conf` | 11 | **Medium** | Binds to `0.0.0.0` with **no TLS**; traffic on the internal bridge is plaintext. A rogue container joining the bridge can eavesdrop. | Enable mTLS for inter-container NATS or add a network-level MAC/container-policy gate. |
| `deploy/nats/nats-local.conf` | 9 | **Medium** | Dev-only config disables **all auth** and runs on localhost; risk of accidental production deployment or symlink swap. | Add a runtime guard in `embedded_nats.py` that refuses `--no_auth` when `LYRA_ENV=prod` or containerenv is detected. |
| `deploy/nats/auth.conf` | 17 | **Low** | Hub and adapters granted broad `$JS.API.>` publish/subscribe permissions; workers also get wide `$JS.API.>` + `$KV.lyra-state.>` access. | Tighten to per-operation subjects (tracked in #1293). |
| `src/lyra/tools/gh_token/dispenser.py` | 92 | **Low** | Unix socket is created with the process umask (typically `0o700`), then `chmod`’d to `0o660` — a TOCTOU race exists between bind and chmod. | Set `umask(0o077)` before `asyncio.start_unix_server`, or create the socket manually with the correct mode. |
| `src/lyra/tools/gh_token/daemon.py` | 88 | **Low** | `LYRA_GH_PEM_PATH` is only checked with `is_file()`; no path-traversal validation. | Validate the PEM path is within a trusted directory. |
| `src/lyra/bootstrap/infra/embedded_nats.py` | 55 | **Low** | Embedded NATS is started with `--no_auth` for zero-config dev convenience. | Refuse to start in prod; emit a loud warning at boot if `--no_auth` is active. |
| `src/lyra/bootstrap/infra/health.py` | 82 | **Low** | `/health` is unauthenticated and returns `{"ok": True}`, confirming process presence to network scanners. | Return a minimal `200 OK` with no body, or require the same bearer token as `/health/detail`. |

---

### Metrics
| Metric | Value |
|--------|-------|
| Files analyzed | 28 bootstrap `.py` + 5 GH-token `.py` + 4 NATS deploy configs |
| Total findings | **12** |
| Severity distribution | Medium: 7 / Low: 5 / High or Critical: 0 |
| OWASP categories touched | Broken Access Control (4), Security Misconfiguration (3), Sensitive Data Exposure (1) |
| Path traversal | 4 findings (33%) |
| Credential handling | 3 findings (25%) |
| NATS ACL / transport | 3 findings (25%) |
| Injection (SQL, shell, XXE) | **0** — SQL in `bootstrap_stores.py` is guarded by `_IDENT_RE`; no XML parsers; subprocess calls use static args. |
| SSRF | **0** — all outbound URLs are hardcoded (GitHub API, Telegram API, NATS subjects). |
| Hardcoded secrets | **0** |

---

### Recommendations (prioritized)

1. **Centralize env-var path validation.** Apply the trusted-base pattern (or stricter) to every env-var-derived file path: `LYRA_VAULT_DIR`, `LYRA_AGENT_STORE_PATH`, `LYRA_TURNS_DB`, `LYRA_RUN_SECRETS_DIR` (non-prod), and `LYRA_GH_PEM_PATH`. Extract a single helper and replace ad-hoc `Path(os.environ.get(...))` calls.

2. **Fix `_validate_config_path` anti-pattern.** Switch the trusted-base anchor from `Path.home()` to `pwd.getpwuid(os.getuid()).pw_dir` so the check cannot be trivially bypassed by setting `$HOME`.

3. **Harden NATS transport in containers.** Either enable mTLS in `nats-container.conf` or document a bridge-network admission policy. Add a boot-time guard that aborts if `nats-local.conf` (no auth, no TLS) is detected in a production runtime (`LYRA_ENV=prod` or `/run/.containerenv`).

4. **Sanitize `bot_id` before path interpolation.** In `credentials.py`, restrict `bot_id` to alphanumeric + hyphen/underscore. This prevents both path traversal and filesystem surprises when `bot_id` is used to construct `/run/secrets/bot_token-{bot_id}` filenames.

5. **Fix Unix-socket permission race in GH-token dispenser.** Use `os.umask(0o077)` before `asyncio.start_unix_server`, or create the socket file descriptor manually with mode `0o660` so no transient wider-permission window exists.
