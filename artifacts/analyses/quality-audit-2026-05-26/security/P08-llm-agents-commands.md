# P08 Security Audit — LLM, Agents, Commands, Agent CLI

**Date:** 2026-05-26
**Scope:** `src/lyra/llm/**/*.py`, `src/lyra/agents/**/*.py`, `src/lyra/commands/**/*.py`, `src/lyra/agent_cmd/**/*.py`, `src/lyra/tools/gh_token/**/*.py`, `src/lyra/integrations/**/*.py`, `src/lyra/cli_agent.py`
**Prior audit baseline:** 2026-05-18 (hexagonal/conformance/dead-code). Do not re-report unless regressed.

---

### Summary

- **1 high-severity path traversal** in audio-attachment handling allows arbitrary file read + delete via unvalidated `Attachment.url_or_path_or_bytes`.
- **2 medium gaps:** NATS control-plane commands lack application-level authorization/path validation; `LYRA_VAULT_DIR`-derived DB/agent paths are unvalidated.
- **No hardcoded secrets or tokens** found in P08. GH token dispenser has good TTL/atomic-write hygiene but does not validate PEM file ownership/permissions at load time.

---

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|--------------|
| `src/lyra/agents/simple_agent_prompts.py` | 55 | **High** | `tmp_path = Path(str(audio_attachment.url_or_path_or_bytes))` constructs a filesystem path directly from attachment metadata without validation. A crafted attachment path (e.g., `../../../etc/passwd`) enables arbitrary file read via `read_bytes()` and arbitrary file delete via `tmp_path.unlink(missing_ok=True)` in the `finally` block. | Validate that `url_or_path_or_bytes` is a safe, absolute temp path under a known directory before read/unlink. Reject URLs, file IDs, and relative paths. |
| `src/lyra/llm/llm_client.py` | 156–210 | **Medium** | `reset()`, `resume_and_reset()`, and `switch_cwd()` dispatch control commands to `lyra.clipool.control` via `_pool._transport.call()` with no code-level authorization check and no validation of `cwd` before serialization. If NATS ACLs are misconfigured, an actor with transport access can reset arbitrary pools or switch the worker to an arbitrary directory. | Add caller-authorization gate (e.g., verify the caller owns the target `pool_id`) and validate `cwd` is within an allowed workspace root before encoding the control command. |
| `src/lyra/cli_agent.py` | 30–33 | **Medium** | `_get_db_path()` resolves `config.db` under `LYRA_VAULT_DIR` env var without path validation. `_user_agents_dir()` in `agent_cmd/agents/init.py:18` does the same for the agents seed directory. A manipulated env var redirects DB writes and TOML reads to arbitrary filesystem locations. | Validate that resolved paths are under `Path.home() / ".lyra"` or an explicit allow-list; reject paths outside the trusted base. |
| `src/lyra/agent_cmd/agents/init.py` | 18 | **Medium** | (same root cause as above — unvalidated `LYRA_VAULT_DIR` env var used for agent TOML seeding) | (same as above) |
| `src/lyra/commands/add_vault/handlers.py:50` + `src/lyra/integrations/vault_cli.py:64–68` | 50 / 64–68 | **Low–Medium** | `title = content[:80]` is derived from user input and passed as a CLI arg to `vault put --title <title>`. `_SAFE_CLI_ARG_RE` guards `category` and `entry_type` but **not** `title`. While `create_subprocess_exec` prevents shell injection, the downstream `vault` CLI could mishandle titles beginning with `-` or containing special sequences. | Extend `_SAFE_CLI_ARG_RE` (or a stricter variant) to `title` before passing it as a CLI argument, or pass `--` before the title if the CLI supports it. |
| `src/lyra/commands/svc/handlers.py` | 40 | **Low** | `_sanitize_svc_output` regex `/(?:home\|var\|tmp\|etc\|usr\|opt)/\S+` does not cover other sensitive absolute paths such as `/root/`, `/proc/`, `/sys/`, `/run/`, or `/dev/`. `systemctl status` output may leak internal paths or sensitive file locations. | Expand the regex (or switch to an allow-list approach) to redact all absolute paths, not just a subset. |
| `src/lyra/tools/gh_token/helper.py` | 85–86 | **Low** | `JWTSigner` reads the PEM private key with `pem_path.read_bytes()` without checking file ownership or permissions. A world-readable or incorrectly owned PEM file could be exploited by a compromised co-located process. | Stat the PEM file before reading; reject if mode is not `0o400` or owner does not match the expected UID. Log a security warning on failure. |
| `src/lyra/agents/simple_agent.py` | 136–142 | **Low** | `SimpleAgent._register_session_commands` catches `Exception` broadly when constructing `SessionTools`. A misconfiguration (e.g., vault CLI missing, web-intel path wrong) is logged at WARNING and the processor pipeline is silently disabled, masking security-relevant operational failures. | Catch specific, expected exceptions only; let unexpected/config-security errors propagate or escalate to an alert. |

---

### Metrics

- **Files analyzed:** 28
- **Security findings:** 7
- **Severity distribution:**
  - High: 1 (14%)
  - Medium: 2 (29%)
  - Low–Medium: 1 (14%)
  - Low: 3 (43%)
- **OWASP Top 10 coverage in P08:** A01, A03, A05, A07, A09 (5/10 categories)
- **Categories with no findings in this partition:** A02 (no custom crypto), A04 (no design-level flaws), A06 (dependency review out of scope), A08 (no integrity-check gaps), A10 (SSRF prevented in P08 by hardcoded GH API URL; boundary risk exists in `WebIntelScraper` which lives outside P08)

---

### Recommendations (prioritized)

1. **Fix audio-attachment path traversal** (`simple_agent_prompts.py:55`) — validate attachment paths are absolute and under a trusted temp directory before `read_bytes()`/`unlink()`.
2. **Validate `LYRA_VAULT_DIR`-derived paths** (`cli_agent.py`, `agent_cmd/agents/init.py`) — enforce that resolved DB and agent directories are under the expected home-base or an explicit allow-list.
3. **Add authorization + path validation to NATS control plane** (`llm_client.py:156–210`) — verify pool ownership before dispatching `reset`/`switch_cwd` and validate `cwd` is within an allowed workspace root.
4. **Extend CLI arg safety to vault title** (`vault_cli.py`) — apply the same `_SAFE_CLI_ARG_RE` guard to `title` that `category` and `entry_type` already receive.
5. **Harden GH token PEM load** (`gh_token/helper.py:85`) — stat-check mode/owner before loading the RSA private key and fail closed on misconfiguration.
