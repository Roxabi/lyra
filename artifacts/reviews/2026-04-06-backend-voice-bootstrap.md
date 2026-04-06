# Backend Review — Voice Decoupling + Bootstrap — 2026-04-06

Commits reviewed: e0bd438, 746b4af, 817fd9e

---

## Overall Verdict

**PASS with 3 WARNs** — Decoupling is architecturally sound. Hub is fully voicecli-free. Bootstrap is clean. Two operational gaps and one defensive issue deserve attention before the next production incident.

---

## STT/TTS NATS Decoupling

### Is STT/TTS fully out of the hub?

Yes. Verified exhaustively:

- `src/lyra/core/hub/hub.py` — `_stt: STTProtocol | None`, `_tts: TtsProtocol | None`. No `STTService`/`TTSService` at class level or runtime (only TYPE_CHECKING guards).
- `src/lyra/core/audio_pipeline.py` — runtime imports in exception handlers: `from ..stt import STTUnavailableError` (line 135 in middleware_stt.py, audio_pipeline.py:205), `from ..tts import TtsUnavailableError` (audio_pipeline.py:400). These import protocol/error types only — no voicecli.
- `src/lyra/bootstrap/agent_factory.py` — imports `STTProtocol`, `TtsProtocol` (lines 20–21). Protocol types only, no service.
- `src/lyra/agents/anthropic_agent.py`, `simple_agent.py` — `STTProtocol`, `TtsProtocol` under TYPE_CHECKING. Clean.
- `src/lyra/bootstrap/voice_overlay.py` — completely rewritten. No STTService/TTSService/voicecli imports. Returns `NatsSttClient | None` and `NatsTtsClient | None`.
- `src/lyra/bootstrap/multibot.py` — sets `stt_service = None`, `tts_service = None` (deprecated path, ADR-039 acknowledged).

**Conclusion:** hub never touches voicecli. The boundary is clean.

### How does the hub request STT/TTS?

NATS request-reply pattern:

- STT: `NatsSttClient.transcribe()` → `nc.request("lyra.voice.stt.request", payload, timeout=60s)` → waits for reply-to inbox.
- TTS: `NatsTtsClient.synthesize()` → `nc.request("lyra.voice.tts.request", payload, timeout=30s)` → waits for reply-to inbox.
- Both clients are instantiated in `voice_overlay.init_nats_stt(nc)` / `init_nats_tts(nc, stt_client)` and injected into `Hub(stt=..., tts=...)`.

### Is the voice adapter a proper hexagonal adapter?

Substantially yes:

- Port: `STTProtocol` / `TtsProtocol` in `src/lyra/stt/__init__.py` and `src/lyra/tts/__init__.py` — `@runtime_checkable` structural protocols with the correct method signatures.
- Hub-side impl: `NatsSttClient` / `NatsTtsClient` in `src/lyra/nats/` implement the protocols via duck-typing (no explicit `implements`).
- Adapter-side: `stt_adapter_standalone.py` / `tts_adapter_standalone.py` in `src/lyra/bootstrap/` — standalone processes that subscribe on NATS and call voicecli.

Minor structural note: `NatsSttClient` and `NatsTtsClient` live in `src/lyra/nats/` rather than a `src/lyra/voice/` domain module. This is pragmatic but slightly blurs nats-infrastructure vs voice-domain. Not a defect.

### What happens if voicecli is not installed / not running?

Two distinct failure modes, both handled correctly:

1. **voicecli not installed** — `stt_adapter_standalone.py` / `tts_adapter_standalone.py` fail at import of `STTService`/`TTSService` from `lyra.stt`/`lyra.tts`. These processes crash at startup. The hub and text adapters continue normally (voice services are optional supervisor programs). The hub receives no NATS replies → `STTUnavailableError`/`TtsUnavailableError` on timeout.

2. **voicecli installed but adapters not running** — Requests time out. `NatsSttClient.transcribe()` raises `STTUnavailableError` after 60s. `AudioPipeline._process_audio_item()` catches it at `audio_pipeline.py:205` and dispatches `stt_unavailable` message to user. `NatsTtsClient.synthesize()` raises `TtsUnavailableError` after 30s. `AudioPipeline.synthesize_and_dispatch_audio()` catches it and falls back to text reply.

**WARN-1:** The STT timeout is 60 seconds (`NatsSttClient.__init__` default). With STT adapter absent, every voice message holds a hub task for the full 60s before degrading. For a single-user deployment this is acceptable; for multi-user it blocks the turn slot. Consider reducing to 10–15s or making it configurable via env var.

### Config field threading

STT per-request overrides (`language_detection_threshold`, `language_detection_segments`, `language_fallback`) are forwarded from `NatsSttClient` constructor fields → NATS request payload → `stt_adapter_standalone.py` handler → `STTService(cfg)` with overrides. Path is correct.

TTS per-agent config survives the NATS boundary: `NatsTtsClient.synthesize()` serialises all `AgentTTSConfig` fields into the flat request dict → `_NatsTtsConfig` dataclass reconstructed in `tts_adapter_standalone.py` → passed as `agent_tts=`. Path is correct.

---

## Embedded NATS Auto-Start (Bootstrap)

### What replaced multibot?

`src/lyra/bootstrap/multibot.py` was deleted. `src/lyra/bootstrap/unified.py` is the new single-process bootstrap. The module renames (`multibot_stores → bootstrap_stores`, etc.) are cosmetic; the underlying wiring is unchanged.

### EmbeddedNats lifecycle

`src/lyra/bootstrap/embedded_nats.py` — EmbeddedNats class:

- `start()`: checks for `nats-server` binary on PATH, launches subprocess with `-a 127.0.0.1 --no_auth`, registers `atexit` handler.
- `wait_ready()`: async TCP probe with `asyncio.open_connection`, 100ms interval, 5s default timeout. Checks `process.returncode` at start of each poll iteration for early failure detection. Correct.
- `stop()`: `terminate()` → 3s wait → `kill()` on timeout → `_deregister_atexit()`. Correct.
- Orphan protection via `atexit._kill_sync()`.

`ensure_nats()` in `embedded_nats.py:144`:
- If `NATS_URL` is unset → auto-starts embedded nats-server, sets `os.environ["NATS_URL"]`, connects.
- If `NATS_URL` is set → connects directly.
- On any failure → `sys.exit()`.
- Returns `(nc, embedded_or_none, nats_url)` wrapped in `finally: nc.close(); embedded.stop()` at call sites.

### Is embedded NATS started correctly in both modes?

- **Unified** (`lyra start` / `_bootstrap_unified`): yes. `ensure_nats(os.environ.get("NATS_URL"))` called at top of function. `embedded.stop()` in the `finally` block at `unified.py:290-297`.
- **Three-process hub** (`lyra hub` / `_bootstrap_hub_standalone`): no embedded NATS. Hub requires `NATS_URL` to be set and calls `sys.exit()` if absent (`hub_standalone.py:146-151`). This is intentional — three-process mode assumes a real NATS server. Documented correctly.
- **Three-process STT/TTS adapters**: same pattern — `NATS_URL` required, `sys.exit()` if absent (`stt_adapter_standalone.py:40-42`, `tts_adapter_standalone.py:69-71`).

**WARN-2:** `ensure_nats` has a latent return-type gap. The function signature declares `-> tuple[NATS, EmbeddedNats | None, str]` but `NATS` is imported only under `TYPE_CHECKING`. At runtime the annotation is a forward-reference string. Pyright resolves this correctly, but the `nc` variable returned is untyped at the call site unless callers also import `NATS` under TYPE_CHECKING. This is cosmetic but can cause confusing type errors downstream.

### Cleanup / shutdown

- `unified.py` `finally` block: `await nc.close()` then `await embedded.stop()` then `_release_lockfile()`. Order is correct — close NATS client before stopping server.
- `EmbeddedNats.stop()` deregisters atexit handler on clean shutdown to avoid double-kill.
- If `nc.close()` raises, the exception is caught and logged; `embedded.stop()` still runs. Correct defensive pattern.

---

## Deploy SHA Cache Fix

### What was the loop?

With a failing test in staging, the deploy timer ran every ~70s and did:
`git fetch` → SHA differs → `git pull` → `uv sync` → `pytest` fails → `git reset --hard` → SHA differs again (reset doesn't change origin/staging) → repeat.

1390+ cycles were observed before the fix.

### Is the fix correct?

Yes. The logic is:

```bash
if [ -f "$FAIL_FILE" ] && grep -Fxq "$LYRA_REMOTE" "$FAIL_FILE"; then
    : # skip silently
else
    # pull / test / on fail: echo "$LYRA_REMOTE" >> "$FAIL_FILE"; reset
fi
```

`grep -Fxq` does exact full-line match — correct for SHA format. `>>` appends, so multiple failures accumulate without overwriting. File is in `~/.local/state/lyra/` which is machine-local and gitignored by convention.

**WARN-3 (operational gap):** `FAIL_FILE` is never cleaned up on successful deploy. If `staging` moves forward and the new SHA passes, `LYRA_UPDATED=true` and `LYRA_UPDATED=true` is set at `deploy.sh:54`. But the file is never truncated or deleted. The comment says "Cleared automatically when staging moves forward to a new SHA" — **this is incorrect**. The file only grows. Consequence: if the same SHA appears in a re-tagged branch or a rebase scenario, it would be incorrectly skipped. Suggested fix:

```bash
# After LYRA_UPDATED=true (line 54):
[ -f "$FAIL_FILE" ] && > "$FAIL_FILE"  # clear on successful deploy
```

**Edge case — deploy after rollback leaves working tree dirty:** `git reset --hard "$LYRA_LOCAL"` rolls back code but leaves `LYRA_LOCAL` at the old SHA. Next run: `git fetch` updates `origin/staging`, `LYRA_LOCAL` still points to old SHA, `LYRA_REMOTE` is the new SHA. The FAIL_FILE check fires only for `$LYRA_REMOTE` — a new SHA not in the file. This is correct: new SHAs always get a fresh attempt.

**Edge case — what if `git reset --hard` fails?** `set -euo pipefail` is set at line 8, but both reset and subsequent `uv sync` pipe through `tee` which consumes the exit code. `timeout 120 ... | tee ...` — the exit code of the pipeline is the exit code of `tee`, not `pytest`, unless `set -o pipefail` is set. It is (`pipefail` is in `set -euo pipefail`), so `pytest` exit code correctly propagates. The reset block does not use a pipeline, so `set -e` applies cleanly.

**Minor:** The `voicecli` update path in `deploy.sh:68-80` has no FAIL_FILE analog. A failing voicecli update would re-trigger on every run. Lower risk since voicecli has no test suite at that point in the script, but worth noting.

---

## Backward Compatibility

### Existing deployments using old voice setup

Before e0bd438, voice was either in-process (multibot mode, voicecli as library) or disabled. After:

- **Three-process production deployment:** hub no longer loads voicecli. STT/TTS requires `lyra_stt` and `lyra_tts` supervisor programs running. New supervisor configs exist (`deploy/supervisor/conf.d/lyra_stt.conf`, `lyra_tts.conf`). If those programs are not registered/started, voice degrades gracefully (timeout → text fallback). No hard break.
- **multibot mode (single process):** `multibot.py` explicitly sets `stt_service = None, tts_service = None`. Voice silently disabled in this mode. multibot is deprecated (ADR-039) and will be deleted in a subsequent PR.
- **`lyra start` (unified):** calls `init_nats_stt(nc)` which returns `None` unless `STT_MODEL_SIZE` env var is set. No voice unless env + adapters are running. Zero-config still works for text-only.

**Conclusion:** No hard compatibility break. Deployments without `lyra_stt`/`lyra_tts` registered degrade to text-only, which is the same behaviour as before for users who had voice disabled.

### `register` make target

`Makefile` `register` target was updated to include `lyra_stt.conf` and `lyra_tts.conf`. Existing production machines that have already run `make register` will not auto-get the new confs — they need a manual `make register` re-run or manual `supervisorctl update`. This is expected operational procedure, not a code bug.

---

## Bugs / Issues Found

| Rank | Severity | File:Line | Description |
|------|----------|-----------|-------------|
| 1 | WARN | `scripts/deploy.sh:54` | `FAIL_FILE` never cleared on successful deploy. Comment claims auto-clear; behaviour is append-only. Stale entries accumulate indefinitely. |
| 2 | WARN | `src/lyra/nats/nats_stt_client.py:27` | STT default timeout is 60s. With adapter absent, each voice message holds a hub task slot for 60s before degrading. No env-var override. |
| 3 | WARN | `src/lyra/bootstrap/embedded_nats.py:17-18` | `NATS` type used in `ensure_nats` return annotation is under `TYPE_CHECKING` only. Forward reference in annotation is fine for type checkers but can produce confusing `NameError`-adjacent warnings in some runtimes if annotation evaluation is triggered. Lower risk with `from __future__ import annotations` (file has it). Non-blocking. |
| 4 | INFO | `scripts/deploy.sh:68-80` | voiceCLI update path has no FAIL_FILE guard. A broken voicecli commit retriggers sync on every timer run (no rollback either — no SHA pinning). |
| 5 | INFO | `src/lyra/bootstrap/hub_standalone.py:488` | Comment `# Note: legacy_audio_handler has no stop() in Slice 1` is a deferred cleanup acknowledged in code. Not a bug, but Slice 2 work item to track. |

---

## Recommended Actions

1. **`scripts/deploy.sh` — clear FAIL_FILE on success** (WARN-1, deploy.sh:54):
   After `LYRA_UPDATED=true` is set, add `[ -f "$FAIL_FILE" ] && > "$FAIL_FILE"` to match the documented "cleared when staging moves forward" contract.

2. **`src/lyra/nats/nats_stt_client.py:27` — expose STT timeout as env var** (WARN-2):
   Add `timeout=float(os.environ.get("LYRA_STT_TIMEOUT", "15"))` in `init_nats_stt()` / `NatsSttClient.__init__`. 15s is sufficient for reasonable audio; 60s is excessive for a degradation path.

3. **`scripts/deploy.sh:68-80` — add FAIL_FILE guard for voicecli** (INFO):
   Mirror the lyra pattern: track `VOICE_REMOTE` in a `VOICE_FAIL_FILE`, skip on known-bad SHA, clear on success.

4. **`src/lyra/nats/nats_bus.py` — unified.py passes no `inbound_audio_bus` to Hub** (tracking note):
   `unified.py` creates `inbound_audio_bus` as a `NatsBus[InboundAudio]` (line 67) but only passes `legacy_audio_handler` to `run_lifecycle`. The `inbound_audio_bus` variable is unused after instantiation. This is consistent with Slice 1 (legacy shim bridges old audio subjects into `inbound_bus`), but the dead variable may confuse future authors. Either remove the `inbound_audio_bus` construction from `unified.py` until Slice 2 needs it, or add an explicit comment.

5. **`src/lyra/bootstrap/hub_standalone.py:488` — add `stop()` to `InboundAudioLegacyHandler`** (Slice 2 prerequisite):
   The Slice 1 comment acknowledges this. When Slice 2 migrates audio, the legacy handler teardown must be explicit.
