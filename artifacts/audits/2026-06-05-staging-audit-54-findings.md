# Audit roxabi-factory staging — 2026-06-05

**Scope:** code merged into `staging` between 2026-06-01 and 2026-06-05 (41 PRs, 1 413 files, ~38k insertions / ~12k deletions)  
**Method:** 5 parallel domain agents (deploy/infra, NATS/ACL, core/inbound, adapters, security/tools) + synthesis  
**Total findings:** 54 — 9 high, 19 medium, 26 low

---

## Summary

| Severity | Count | Categories |
|----------|-------|------------|
| **high** | 9 | bug, race-condition, error-handling |
| **medium** | 19 | bug, security, error-handling, architecture, test-gap |
| **low** | 26 | bug, race-condition, error-handling, architecture, performance, security |

---

## High severity

### 1. `turns.db` host path mismatch + directory creation risk
- **File:** `deploy/quadlet/factory-turn-writer.container` (line 29)
- **Category:** bug
- **Description:** `turn-writer` writes to `~/.roxabi/factory/turn-writer/turns.db` (mounts `turn-writer/` → `/data/`, `FACTORY_TURNS_DB=/data/turns.db`), but `factory-hub.container` mounts `~/.roxabi/factory/turns.db` at the root. They are not the same file. Additionally, `install.sh` does not pre-create `turns.db`, so on first boot Podman creates it as a directory. The hub then reads a directory instead of a SQLite database.
- **Recommendation:** Align the paths: either mount `~/.roxabi/factory/turn-writer/turns.db` as read-only in the hub, or have turn-writer write to `~/.roxabi/factory/turns.db`. Also add `install.sh` logic to pre-create a valid SQLite file or symlink.

### 2. `turns.db` directory creation risk in hub
- **File:** `deploy/quadlet/factory-hub.container` (line 40)
- **Category:** bug
- **Description:** `Volume=%h/.roxabi/factory/turns.db:...` — if `turns.db` does not exist on the host, Podman creates it as a directory on first boot. The hub then reads a directory instead of a SQLite database. `install.sh` only creates `turn-writer/` directory, not `turns.db`.
- **Recommendation:** Add `install.sh` step to pre-create `turns.db` as a valid SQLite file (or a symlink to `turn-writer/turns.db`) before units start, or change the hub mount to point at `turn-writer/turns.db`.

### 3. `push_to_hub_guarded` misses `KeyError` for unregistered platforms
- **File:** `src/factory/core/messaging/push_guard.py` (line 79)
- **Category:** error-handling
- **Description:** `push_to_hub_guarded` catches `asyncio.QueueFull` but not `KeyError`. `NatsBus.put` and `LocalBus.put` both raise `KeyError` for an unregistered platform. The docstring claims "Always returns normally" but `KeyError` propagates. `Dispatcher.dispatch` also does not catch `KeyError` (including the one raised by `Platform[msg.platform.upper()]` before `push_to_hub_guarded` is called). An unregistered or misconfigured platform crashes the adapter inbound handler.
- **Recommendation:** Add `except KeyError` in `push_to_hub_guarded` with `logger.warning` and send backpressure. Also guard `Platform[msg.platform.upper()]` in `Dispatcher.dispatch` with a `try/except KeyError`.

### 4. Telegram adapter — infinite webhook retry on unhandled exception
- **File:** `src/factory/adapters/telegram/telegram_inbound.py` (line 141)
- **Category:** error-handling
- **Description:** `handle_message` only catches `AttachmentIngestError` around `_pipeline.run`. The inline comment explicitly states "Never raise here or Telegram will retry the update indefinitely", but any other exception (e.g., `KeyError` from unregistered platform, `TypeError` from malformed message) propagates to aiogram and triggers infinite webhook retries. `handle_voice_message` at line 296 has the same gap.
- **Recommendation:** Wrap the entire `_pipeline.run` call in a broad `except Exception` with `logger.exception` to ensure the adapter always returns normally.

### 5. Discord adapter — gateway reconnect loop on unhandled exception
- **File:** `src/factory/adapters/discord/discord_inbound.py` (line 338)
- **Category:** error-handling
- **Description:** `handle_message` only catches `AttachmentIngestError` around `_pipeline.run`. Any other exception (e.g., `KeyError` from unregistered platform) propagates to discord.py and crashes the gateway connection, triggering a reconnect loop.
- **Recommendation:** Wrap the entire `_pipeline.run` call in a broad `except Exception` with `logger.exception` to ensure the adapter always returns normally.

### 6. `seed_watch_channels` — `js.key_value` outside timeout
- **File:** `src/factory/bootstrap/wiring/kv_watch_channels.py` (line 111)
- **Category:** race-condition
- **Description:** `seed_watch_channels` uses `asyncio.timeout(timeout)` only around `kv.get(key)`. The `js.key_value(_BUCKET)` call at line 111 is outside the timeout. If NATS is unresponsive during bucket binding (e.g., network partition, slow JetStream), the adapter startup hangs indefinitely.
- **Recommendation:** Wrap the entire `js.key_value()` + `kv.get()` block inside `asyncio.timeout(timeout)`.

### 7. `KvLastSessionStore.connect` silently degrades on all NATS errors
- **File:** `src/factory/infrastructure/stores/turn_session_kv.py` (line 84)
- **Category:** error-handling
- **Description:** `KvLastSessionStore.connect()` catches `nats.errors.Error` broadly. This includes `ConnectionClosedError`, `PermissionDeniedError`, and many other non-recoverable errors. It silently degrades to `_kv=None`, causing `get_last_session` to return `None` forever and masking real connectivity or permission issues.
- **Recommendation:** Catch only `BucketNotFoundError` for cold-boot degradation. Let all other `nats.errors.Error` propagate so the caller knows the connection is broken.

### 8. Adapter standalone — narrow exception handler misses TLS/timeout errors
- **File:** `src/factory/bootstrap/standalone/adapter_standalone.py` (line 53)
- **Category:** error-handling
- **Description:** NATS connection failure handler `except (nats.errors.Error, OSError)` is too narrow. `nats_connect` can raise `asyncio.TimeoutError`, `ssl.SSLError`, and `ValueError` (e.g. invalid TLS config). These are not caught and bubble up as raw stack traces instead of clean `sys.exit` messages.
- **Recommendation:** Broaden the handler to `except (nats.errors.Error, OSError, asyncio.TimeoutError, ssl.SSLError) as exc:` or use a single `except Exception as exc:` with a comment explaining the boundary catch.

### 9. `gh_token` daemon — `nc.drain()` hang on shutdown
- **File:** `src/factory/tools/gh_token/daemon.py` (line 199)
- **Category:** race-condition
- **Description:** `nc.drain()` in the outer `finally` block has no timeout. If the NATS connection is dead or stuck, `drain()` can hang indefinitely, blocking the entire shutdown sequence. systemd may escalate to SIGKILL after `TimeoutStopSec`, preventing clean teardown and possibly leaving the Unix socket behind.
- **Recommendation:** Wrap `nc.drain()` with `asyncio.wait_for(timeout=5.0)` or `asyncio.timeout` so shutdown is bounded. If `drain()` times out, fall back to `nc.close()` and continue.

---

## Medium severity

### 10. Old `lyra*` files pollute convergence checksum
- **File:** `deploy/lib/deploy-common.sh` (line 45)
- **Category:** bug
- **Description:** `compute_convergence_state` uses `find ... -name 'lyra*' -o -name 'factory*'` to hash Quadlet units. Old `lyra*` files from a previous install may still exist in `~/.config/containers/systemd/`. Their presence (or absence) affects the checksum, causing spurious convergence drift or masking real changes. `install.sh` does not clean up old `lyra*` files during install.
- **Recommendation:** Remove the `-name 'lyra*'` branch from the `find` command (or add cleanup of old `lyra*` files in `install.sh`) so only `factory*` files contribute to the fingerprint.

### 11. `jq` hard dependency not installed by `provision.sh`
- **File:** `deploy/factory-post-autoupdate.sh` (line 26)
- **Category:** bug
- **Description:** `remote_digest()` calls `skopeo ... | jq -r '.Digest'`. `jq` is a hard dependency but `provision.sh` does not install it. If `jq` is missing, the systemd service fails every 5 minutes, triggering `OnFailure=factory-deploy-failure.service` repeatedly.
- **Recommendation:** Add `jq` to the `provision.sh` base package list (e.g. `sudo apt install -y jq`), or add a prerequisite check in `factory-post-autoupdate.sh` with a clear error message.

### 12. `skopeo inspect` without retry logic
- **File:** `deploy/factory-post-autoupdate.sh` (line 27)
- **Category:** bug
- **Description:** `skopeo inspect` is called without `--no-creds` or `--retry-times`. If GHCR returns a rate-limit error (429) or a transient 5xx, `skopeo` exits non-zero and the entire script fails under `set -euo pipefail`. The systemd service has no backoff or retry logic.
- **Recommendation:** Add `skopeo inspect --retry-times 3` (if supported) or wrap the call in a small retry loop with exponential backoff. Alternatively, gate on `skopeo` being available before use.

### 13. `blobstore` identity over-permissioned on `$JS.API.>`
- **File:** `deploy/nats/acl-matrix.json` (line 329)
- **Category:** security
- **Description:** `blobstore` identity carries broad `$JS.API.>` in publish, but its actual JetStream API surface is limited to `js.account_info()` (for `BlobAuditSink`) and KV readiness announce (`js.key_value` / `js.create_key_value` for `factory-state`). The wildcard could be narrowed to `$JS.API.INFO`, `$JS.API.STREAM.INFO.KV_factory-state`, `$JS.API.STREAM.CREATE.KV_factory-state`, and `$JS.API.STREAM.UPDATE.KV_factory-state` without functional loss.
- **Recommendation:** Scope `blobstore` publish to the minimal JS API subjects it actually exercises. Add a CI gate or ADR note that blocks new `$JS.API.>` grants unless explicitly exempted.

### 14. Workers over-permissioned on `$JS.API.>` for readiness probe
- **File:** `deploy/nats/acl-matrix.json` (line 150)
- **Category:** security
- **Description:** `voice-tts`, `voice-stt`, `llm-worker`, `image-worker`, and `clipool-worker` all carry `$JS.API.>` in publish solely for the `wait_for_hub` readiness probe. They only need `$JS.API.STREAM.INFO.KV_factory-state` and `$JS.API.STREAM.MSG.GET.KV_factory-state`. The acl-matrix notes explicitly say "Tighter per-operation scoping tracked in #1293."
- **Recommendation:** Complete #1293 (holistic hardening) by narrowing worker `$JS.API.>` grants to the specific `INFO` + `MSG.GET` pair for `KV_factory-state`. Workers do not provision streams or create KV buckets.

### 15. `check_request_reply_flows.py` only checks one direction
- **File:** `scripts/check_request_reply_flows.py` (line 59)
- **Category:** test-gap
- **Description:** The script only verifies that the requester's publish covers the flow subject. It does not check that the responder's subscribe covers the flow subject, nor that the responder's publish covers the requester's inbox (`_inbox.{requester}.>`) for reply delivery. This means a responder could be missing subscribe or inbox publish grants and the gate would still be green.
- **Recommendation:** Extend `check_request_reply_flows.py` to validate both directions: (1) responder subscribe must cover the flow subject, and (2) responder publish must cover the requester's inbox. This closes the loop on the request-reply invariant.

### 16. `sqlite3.connect` context manager leaks connection
- **File:** `src/factory/bootstrap/bootstrap_stores.py` (line 59)
- **Category:** bug
- **Description:** `_has_sentinel` uses `with sqlite3.connect(...) as conn:`. In Python 3.12, the `sqlite3.Connection` context manager only commits or rolls back on exit; it does NOT close the connection. The connection is leaked.
- **Recommendation:** Use `contextlib.closing(sqlite3.connect(...))` or explicit `conn.close()` in a `finally` block.

### 17. Inconsistent exception granularity in `SessionBuilder` read paths
- **File:** `src/factory/inbound/session_builder.py` (line 93)
- **Category:** error-handling
- **Description:** Inconsistent exception granularity between `SessionBuilder` read paths. `_build_turnstore_path` catches `Exception` broadly (line 93) for `get_last_session`, swallowing `ValueError`, `TypeError`, and other programming errors. `_build_thread_path` catches only `sqlite3.Error`, `RuntimeError` (line 171) for `get_session`. A `ValueError` from the thread path would propagate and crash the pipeline, while the same error from the turnstore path is silently swallowed.
- **Recommendation:** Align the exception granularity. Either catch specific exceptions in both paths (e.g., `sqlite3.Error`, `RuntimeError`, `nats.errors.Error`), or let both propagate with a shared wrapper that logs and degrades gracefully.

### 18. `_adapter_factory` — `StopIteration` not caught
- **File:** `src/factory/bootstrap/wiring/bootstrap_wiring.py` (line 265)
- **Category:** error-handling
- **Description:** `_adapter_factory` uses `next(cfg for cfg, _ in deps.dc_bot_auths if cfg.bot_id == bot_id)` without a default value. If `bot_id` is not found (e.g., due to list mutation or filtering), `StopIteration` is raised. `StopIteration` is not a subclass of `Exception` and will propagate uncaught, crashing the wiring loop.
- **Recommendation:** Use `next(..., None)` and raise a descriptive `ValueError` if `None`.

### 19. `_build_hub` reaches into `NatsBus._nc` private attribute
- **File:** `src/factory/bootstrap/factory/hub/hub_core.py` (line 51)
- **Category:** architecture
- **Description:** `_build_hub` reaches into the private attribute `NatsBus._nc` to call `jetstream()` and to construct `TypingPublisher`. This couples bootstrap to `NatsBus` internals and violates the Bus protocol abstraction. If `NatsBus` ever refactors its internal connection storage, bootstrap will break.
- **Recommendation:** Add a public `jetstream()` method or `nc` property to `NatsBus`, or pass the NATS client directly in `BuildHubDeps` instead of pulling it out of the bus.

### 20. `seed_watch_channels` — `BucketNotFoundError` not caught
- **File:** `src/factory/bootstrap/wiring/kv_watch_channels.py` (line 72)
- **Category:** error-handling
- **Description:** `_open_or_create_kv` catches `BadRequestError` and assumes it means "bucket already exists". If `create_key_value` raises `BadRequestError` for a different reason (e.g., invalid config, permission denied), the fallback `js.key_value(_BUCKET)` will raise `BucketNotFoundError` and the original cause is lost.
- **Recommendation:** Inspect the error message or code to confirm it is a "bucket already exists" error before falling back to `js.key_value()`.

### 21. `seed_watch_channels` — `BucketNotFoundError` crash path in standalone
- **File:** `src/factory/bootstrap/wiring/kv_watch_channels.py` (line 111)
- **Category:** error-handling
- **Description:** `seed_watch_channels` calls `js.key_value(_BUCKET)` which raises `BucketNotFoundError` if the hub has not yet provisioned the `factory-state` bucket. The function only catches `KeyNotFoundError` and `TimeoutError`, leaving a crash path. In `standalone_discord.py` line 182, `seed_watch_channels` is also called outside the `try` block that cleans up previously wired bots, so a missing bucket causes a crash without resource cleanup.
- **Recommendation:** Add `BucketNotFoundError` to the caught exceptions in `seed_watch_channels` (return `frozenset()`). Also move the `seed_watch_channels` call inside the `try` block in `standalone_discord.py` so previously wired bots are cleaned up on failure.

### 22. Inconsistent error handling between `SessionBuilder` update closures
- **File:** `src/factory/inbound/session_builder.py` (line 213)
- **Category:** error-handling
- **Description:** In `_build_thread_path`, the `_thread_update_fn` catches `sqlite3.Error`, `RuntimeError`, logs via `log.exception`, and then re-raises. `_turnstore_update_fn` (defined in `_build_turnstore_path`) does not catch any exceptions. A `nats.errors.Error` from `_publisher.publish_start_session` or `_last_session.set_last_session` in the turnstore path will propagate without logging, while the thread path at least logs before re-raising.
- **Recommendation:** Add a consistent exception handler in both update closures (log + re-raise), or remove the catch from `_thread_update_fn` if both should propagate raw.

### 23. `gh_token` daemon — `FACTORY_GH_RATE_LIMIT_S` parse unguarded
- **File:** `src/factory/tools/gh_token/daemon.py` (line 102)
- **Category:** error-handling
- **Description:** `float(os.environ.get('FACTORY_GH_RATE_LIMIT_S', '10'))` raises `ValueError` for non-numeric or empty string values, but the exception is not caught by `DaemonConfigError`. The daemon crashes with an unhandled exception instead of the documented exit code 2.
- **Recommendation:** Validate the env var inside `_load_config()` with a `try/except ValueError` that raises `DaemonConfigError`, and use the default when the var is an empty string.

### 24. `gh_token` daemon — `JWTSigner` constructed outside config validation
- **File:** `src/factory/tools/gh_token/daemon.py` (line 125)
- **Category:** error-handling
- **Description:** `JWTSigner` is constructed inside `run_daemon()`, not inside `_load_config()`. If the PEM file is password-protected or contains a non-RSA key, `JWTSigner` raises `ValueError`/`TypeError` during `run_daemon()`. This is not caught by `DaemonConfigError`, so the daemon exits with an unhandled exception instead of the documented exit code 2.
- **Recommendation:** Move `JWTSigner(config.pem_path)` into `_load_config()` and catch any `ValueError`/`TypeError`, re-raising as `DaemonConfigError` so the failure is handled at config time.

### 25. `gh_token` daemon — `sock_path` mkdir/unlink unguarded
- **File:** `src/factory/tools/gh_token/daemon.py` (line 176)
- **Category:** error-handling
- **Description:** `config.sock_path.parent.mkdir()` and `config.sock_path.unlink()` are not guarded. If the parent path is a file (not a directory), `mkdir` raises `NotADirectoryError`. If `sock_path` is a directory, `unlink` raises `IsADirectoryError`. Both crash the daemon with unhandled exceptions.
- **Recommendation:** Wrap `mkdir`/`unlink` in a `try/except OSError` that raises `DaemonConfigError` with a clear message so the daemon returns exit code 2.

### 26. `gh_token` daemon — `_safe_machine_name` fallback to `'unknown'`
- **File:** `src/factory/tools/gh_token/daemon.py` (line 138)
- **Category:** security
- **Description:** `_safe_machine_name()` falls back to `'unknown'` for invalid `FACTORY_MACHINE` values. If multiple machines have invalid names, all mint-failure events collide on `factory.gh.mint_failure.unknown`, breaking per-machine observability and making it impossible to identify which host is failing.
- **Recommendation:** Sanitize the machine name (e.g., replace dots with underscores) instead of falling back to `'unknown'`, or reject invalid names at startup with a `DaemonConfigError`.

### 27. `gh_token` daemon — broad `except Exception` around NATS connect hides degraded state
- **File:** `src/factory/tools/gh_token/daemon.py` (line 154)
- **Category:** error-handling
- **Description:** Broad `except Exception` around NATS connect silently degrades observability. If NATS is down or misconfigured, the daemon continues serving tokens but never publishes mint failures. There is no health endpoint or metric to detect this degraded state.
- **Recommendation:** Add a warning log that is visible at startup (already done) and consider incrementing a simple metric or logging a periodic reminder that mint-failure publishing is disabled. Alternatively, expose the publisher state via a lightweight status mechanism.

### 28. Adapter standalone — redundant `vault_dir` mkdir + dead parameter
- **File:** `src/factory/bootstrap/standalone/adapter_standalone.py` (line 79)
- **Category:** architecture
- **Description:** `vault_dir.mkdir(parents=True, exist_ok=True)` is redundant because `standalone_discord.py` already calls `discord_dir.mkdir(parents=True, exist_ok=True)` which creates the parent `~/.roxabi/factory` as a side effect. Additionally, `vault_dir` is passed to `bootstrap_discord_standalone` at line 85 but is completely unused inside that function (the function computes `discord_dir = factory_discord_data_dir()` independently). This is leftover wiring from the TurnStore removal refactor.
- **Recommendation:** Remove `vault_dir` from `adapter_standalone.py` and `bootstrap_discord_standalone` signatures; let `standalone_discord.py` handle the single `mkdir` at `factory_discord_data_dir()`.

---

## Low severity

### 29. `systemd-cat` message still references `lyra`
- **File:** `deploy/systemd/factory-deploy-failure.service` (line 6)
- **Category:** bug
- **Description:** The `systemd-cat` message still says "Lyra deploy failure" and references `lyra-quadlet-sync` / `lyra-post-autoupdate`. The service and tag were renamed to `factory-deploy-failure`. Stale text makes log-based alerting harder to correlate.
- **Recommendation:** Update the message to "Factory deploy failure: a deployment-related service (factory-quadlet-sync or factory-post-autoupdate) failed..."

### 30. `printf %s` unquoted format string in `ExecStartPre`
- **File:** `deploy/quadlet/factory-blobstore.container` (line 76)
- **Category:** bug
- **Description:** `ExecStartPre` uses `printf %s` without quoting the format string. If `TAILSCALE_IPV4` somehow contains a `%` character, `printf` will misinterpret it. While the value is normally an IP address, this is a latent shell-safety issue.
- **Recommendation:** Change `printf %s` to `printf '%s'` inside the `ExecStartPre` command.

### 31. `.claude/.credentials.json` created without parent directory
- **File:** `deploy/quadlet/factory-clipool.container` (line 85)
- **Category:** bug
- **Description:** `ExecStartPre=/bin/bash -c 'test -e %h/.claude/.credentials.json || echo "{}" > %h/.claude/.credentials.json'` — if the `%h/.claude` directory does not exist on the host, the `echo` redirection fails. The `ExecStartPre` runs before the container (and its volume mounts) are active. `provision.sh` touches `clipool.env` but does not ensure `.claude/.credentials.json` exists.
- **Recommendation:** Add `mkdir -p %h/.claude` before the `echo` in the same `ExecStartPre`, or have `provision.sh` create the file and directory.

### 32. `factory bot init` uses wrong image tag (`:staging-svc`)
- **File:** `deploy/install.sh` (line 309)
- **Category:** bug
- **Description:** `podman run ... ghcr.io/roxabi/factory:staging-svc factory bot init` uses the `staging-svc` (service-runtime) image tag to run a CLI command. The docs define `staging-svc` as the service runtime variant and `staging` as the agent/CLI runtime variant. If the `staging-svc` image lacks the `factory` CLI entrypoint, this step will fail.
- **Recommendation:** Use `ghcr.io/roxabi/factory:staging` for the `factory bot init` CLI invocation, or verify that `staging-svc` includes the CLI entrypoint.

### 33. `converge.sh` NATS poll timeout too short
- **File:** `deploy/converge.sh` (line 59)
- **Category:** race-condition
- **Description:** The `is-active` poll for `factory-nats` waits at most 10 seconds. If the NATS container is slow to start (e.g., cold image pull on a slow network), the poll times out and the converge aborts, even though systemd may eventually bring the unit to active state.
- **Recommendation:** Increase the poll timeout (e.g., 30–60 seconds) or gate on `systemctl --user is-failed` to distinguish between "still starting" and "failed".

### 34. `ufw` rule hardcodes LAN subnet
- **File:** `deploy/nats/setup.sh` (line 93)
- **Category:** security
- **Description:** `sudo ufw allow from 192.168.1.0/24 to any port 4222 proto tcp` hardcodes the LAN subnet. On a host where the LAN is different (e.g., 10.0.0.0/24), this rule is useless and the port remains blocked. This is a security/config mismatch.
- **Recommendation:** Derive the LAN subnet from the default gateway interface (e.g., `ip route | awk '/default/ {print $3}' | xargs -I {} ip route | grep {} | awk '{print $1}'`) or make it an overridable env var with a default.

### 35. NATS image tag-only (no digest) — doc/code drift
- **File:** `deploy/quadlet/factory-nats.container` (line 7)
- **Category:** bug
- **Description:** Image is `docker.io/library/nats:2.10.29-alpine` (tag-only, no digest). `deploy/CLAUDE.md` explicitly states: "NATS version: pinned by digest in factory-nats.container — ¬autoupdate, bump manually."
- **Recommendation:** Either pin the image by digest in `factory-nats.container` to match the documented invariant, or update `CLAUDE.md` to reflect tag-only pinning.

### 36. `git config` runs as current user, not `ADMIN_USER`
- **File:** `deploy/provision.sh` (line 526)
- **Category:** bug
- **Description:** `git config --global user.name` and `user.email` are run as the current shell user, not as `ADMIN_USER`. If the script is invoked with `ADMIN_USER=anotheruser` (e.g. via `sudo` or `su`), the git config is written to the wrong user's `~/.gitconfig`.
- **Recommendation:** Run `sudo -u "$ADMIN_USER" git config --global ...` instead of `git config --global ...`.

### 37. `ssh-keyscan` stderr swallowed, empty `known_hosts` possible
- **File:** `deploy/provision.sh` (line 368)
- **Category:** bug
- **Description:** `ssh-keyscan github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null` swallows all stderr. If `ssh-keyscan` fails (network blip, DNS error), the command still exits 0 and may create an empty or malformed `known_hosts` file. The subsequent SSH authentication check will then fail with a less obvious error.
- **Recommendation:** Remove `2>/dev/null` (or redirect stderr to a temporary variable) and verify the output is non-empty before appending. Alternatively, check the exit code of `ssh-keyscan` explicitly.

### 38. Redundant `.*.*` in `_inbox` entries
- **File:** `deploy/nats/acl-matrix.json` (line 101)
- **Category:** architecture
- **Description:** `telegram-adapter` and `discord-adapter` both list `_inbox.telegram-adapter.>` and `_inbox.telegram-adapter.*.*` (and similarly for discord). The `.>` suffix already covers `.*.*` (any number of sub-tokens). The redundant `.*.*` adds noise to the matrix and auth.conf without changing effective grants.
- **Recommendation:** Remove the redundant `.*.*` entries from adapter subscribe lists to keep the matrix minimal and readable.

### 39. Stale `lyra` path in `nats.conf` comment
- **File:** `deploy/nats/nats.conf` (line 4)
- **Category:** architecture
- **Description:** Line 4 contains a stale install path: `lyra/deploy/nats/install.sh`. The project was renamed from `lyra` to `roxabi-factory`. Line 50 also references the retired `lyra-monitor` service.
- **Recommendation:** Update the comment on line 4 to `roxabi-factory/deploy/nats/install.sh` and line 50 to remove the stale `lyra-monitor` reference or update it to current monitoring state.

### 40. Stale `lyra-monitor` reference in `nats-container.conf`
- **File:** `deploy/nats/nats-container.conf` (line 38)
- **Category:** architecture
- **Description:** Line 38 says "Polled by lyra-monitor check_nats_varz()". The `lyra-monitor` service/timer was removed from deploy (per acl-matrix monitor identity notes). The reference is stale and could confuse operators.
- **Recommendation:** Update the comment to reflect current monitoring state or remove the stale reference.

### 41. `Owner` type literal still includes `"lyra"`
- **File:** `scripts/_acl_models.py` (line 6)
- **Category:** architecture
- **Description:** The `Owner` type literal still includes `"lyra"`, but no active identity in `acl-matrix.json` uses `lyra` as owner. All identities have migrated to `factory`, `voicecli`, `imagecli`, or `reserved`.
- **Recommendation:** Remove `"lyra"` from the `Owner` literal to keep the type model aligned with the matrix.

### 42. `_DDL_INDEX_RE` recompiled inside loop
- **File:** `src/factory/bootstrap/bootstrap_stores.py` (line 143)
- **Category:** performance
- **Description:** `_DDL_INDEX_RE` is compiled inside the loop over `idx_rows` in `_atomic_table_copy`. The number of indices is small but the regex is recompiled for every iteration unnecessarily.
- **Recommendation:** Compile the regex once at module level.

### 43. Temp file not cleaned up on `_atomic_table_copy` failure
- **File:** `src/factory/bootstrap/bootstrap_stores.py` (line 93)
- **Category:** bug
- **Description:** `_atomic_table_copy` creates a temp file with `tempfile.mkstemp`. If the try block fails (e.g., invalid column name), the `finally` block closes DB connections but the temp file is left behind on disk. There is no cleanup of the incomplete temp file.
- **Recommendation:** Add `tmp_path.unlink(missing_ok=True)` in the `finally` block (or in a separate `except` block) to clean up the temp file on failure.

### 44. `KvLastSessionStore.set_last_session` catches broad `nats.errors.Error`
- **File:** `src/factory/infrastructure/stores/turn_session_kv.py` (line 113)
- **Category:** error-handling
- **Description:** `KvLastSessionStore.set_last_session` catches `nats.errors.Error` broadly on `kv.put`. This includes `PermissionDeniedError`, `BadRequestError`, etc., and silently logs a warning. A permission denied or misconfiguration will be masked as a transient warning.
- **Recommendation:** Catch specific exceptions (e.g., `ConnectionClosedError`, `TimeoutError`) instead of the broad `nats.errors.Error` base class.

### 45. Hardcoded cache size 500 in `SessionBuilder`
- **File:** `src/factory/inbound/session_builder.py` (line 166)
- **Category:** architecture
- **Description:** `_build_thread_path` uses a hardcoded cache size of 500 for `ctx.thread_sessions_cache`. This is a magic number with no configuration knob or named constant.
- **Recommendation:** Extract to a named constant (e.g., `_THREAD_SESSION_CACHE_SIZE = 500`) or make it configurable via `ctx`.

### 46. `_platform_enum` silently defaults to `TELEGRAM`
- **File:** `src/factory/inbound/session_builder.py` (line 235)
- **Category:** error-handling
- **Description:** `_platform_enum` defaults to `Platform.TELEGRAM` for any unknown platform string. A misconfigured platform (e.g., typo in config) will be silently routed to the TELEGRAM pool, causing misrouting and confusing logs.
- **Recommendation:** Raise `ValueError` for unknown platforms instead of silently defaulting to `TELEGRAM`.

### 47. Duplicate log entries for `ThreadStore` failure
- **File:** `src/factory/inbound/session_builder.py` (line 218)
- **Category:** error-handling
- **Description:** In `_build_thread_path`, the `_thread_update_fn` catches `sqlite3.Error` and `RuntimeError`, logs via `log.exception`, and then re-raises. The caller (`pool_observer.session_update_async`) catches all exceptions and logs again with `log.error`. This produces duplicate log entries for the same `ThreadStore` failure.
- **Recommendation:** Remove the `raise` in `_thread_update_fn` and let the single broad-catch site in `pool_observer` handle logging, consistent with `_turnstore_update_fn` which does not re-raise.

### 48. `SessionCtx.turn_store` is dead code
- **File:** `src/factory/inbound/context.py` (line 74)
- **Category:** architecture
- **Description:** `SessionCtx.turn_store` is declared as a field but all adapters (Telegram and Discord) always pass `None`. `SessionBuilder` never actually uses the `turn_store` object for store operations; it uses `last_session` (via `KvLastSessionStore` or `TurnStoreLastSession`) for all session persistence. The docstring in `session_builder.py` lines 31-32 still describes paths (b) and (c) as `turn_store`-only, which is stale after the TurnStore removal from adapters.
- **Recommendation:** Either remove `turn_store` from `SessionCtx` since it is dead code in the adapter-only pipeline, or update the `SessionBuilder` docstring to reflect that `last_session` is the active path and `turn_store` is a legacy field.

### 49. `dc_kv_last_session` never closed in Discord teardown
- **File:** `src/factory/bootstrap/wiring/standalone_discord.py` (line 112)
- **Category:** architecture
- **Description:** `dc_kv_last_session` is created and connected but never explicitly closed in the teardown path. While `KvLastSessionStore.close()` is currently a no-op (it only sets `_kv = None`), it is a lifecycle gap that will break if the class ever acquires real resources (e.g., background tasks, connection pool).
- **Recommendation:** Pass `dc_kv_last_session` to `_bootstrap_discord_teardown` and call `await dc_kv_last_session.close()` in the `finally` block of `bootstrap_discord_standalone`.

### 50. `tg_kv_last_session` never closed in Telegram teardown
- **File:** `src/factory/bootstrap/wiring/standalone_telegram.py` (line 92)
- **Category:** architecture
- **Description:** Same lifecycle gap as the Discord standalone: `tg_kv_last_session` is created and connected but never closed. `KvLastSessionStore.close()` is a no-op today, but the resource should be closed for completeness.
- **Recommendation:** Add `await tg_kv_last_session.close()` in the `finally` block of `bootstrap_telegram_standalone` or in `_bootstrap_telegram_teardown`.

### 51. `task.cancel()` redundant in daemon finally
- **File:** `src/factory/tools/gh_token/daemon.py` (line 226)
- **Category:** error-handling
- **Description:** `task.cancel()` is called in the `finally` block after `asyncio.gather(task)` already raised `CancelledError`. The task is already cancelled, so `task.cancel()` is redundant and harmless but unnecessary.
- **Recommendation:** Remove `task.cancel()` from the `finally` block; the `gather` already handles cancellation.

### 52. `_shutdown` may cancel already-cancelled tasks
- **File:** `src/factory/tools/gh_token/daemon.py` (line 209)
- **Category:** error-handling
- **Description:** `_shutdown()` cancels all tasks via `asyncio.all_tasks(loop)`. If a signal arrives while the loop is already shutting down (e.g., rapid SIGTERM + SIGINT), tasks may be cancelled twice. This is harmless but noisy.
- **Recommendation:** Add a guard in `_shutdown()` to skip tasks that are already cancelled: `if not task.done() and not task.cancelled(): task.cancel()`.

### 53. `os.chmod` TOCTOU on Unix socket
- **File:** `src/factory/tools/gh_token/dispenser.py` (line 98)
- **Category:** race-condition
- **Description:** `os.chmod(sock_path, 0o660)` is called after `asyncio.start_unix_server()`. There is a TOCTOU window between socket creation and `chmod`. If an attacker races to create a symlink at `sock_path`, `chmod` could target the wrong file. The risk is low because `sock_path` is in a `0700` tmpfs directory.
- **Recommendation:** Use `socket.bind()` directly with a pre-created socket set to `0o660` before bind, or verify the path is a socket (`stat.S_ISSOCK`) before `chmod`.

### 54. `mint()` misses `httpx.StreamError` / `StreamClosed`
- **File:** `src/factory/tools/gh_token/helper.py` (line 220)
- **Category:** error-handling
- **Description:** `mint()` catches `httpx.ConnectError` and `httpx.TransportError` but does not catch `httpx.StreamError` or `httpx.StreamClosed`. These are uncommon in practice but could be raised by the HTTP/2 layer. If they propagate, the client connection is dropped without an error response because `_handle()` only catches `MintError`.
- **Recommendation:** Broaden the exception catch in `mint()` to `except httpx.RequestError as exc` (the parent of `TransportError` and other httpx errors), or add a fallback `except httpx.HTTPError` to ensure all httpx errors are mapped to `MintError`.

---

*Generated by multi-agent workflow — 5 domain auditors + synthesis.*
