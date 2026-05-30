# 1521 — Audio NATS Contract Axial Migration

ADR: `docs/architecture/adr/079-audio-nats-contract-axial-consolidation.mdx`
Tracking: #1521
Relief PR partially superseded: #1520 — S3 removes `STREAM.CREATE.LYRA_OUTBOUND_AUDIO`,
`STREAM.UPDATE.LYRA_OUTBOUND_AUDIO`, and `STREAM.CREATE.KV_lyra_outbound_audio_sent`
from adapter identities (these move to hub sole ownership). The remaining #1520 grants
(`STREAM.INFO.LYRA_OUTBOUND_AUDIO`, `STREAM.INFO/MSG.GET.KV_…`, `$JS.ACK.LYRA_OUTBOUND_AUDIO.>`,
`CONSUMER.CREATE.LYRA_OUTBOUND_AUDIO.>`) stay on adapters; S4 moves them into the
`audio-consumer` group for structural deduplication.

## Overview

Four implementation slices, each independently shippable and verifiable.
**S2 ships first** as a safety-net: it prevents future crash-loops before
the ACL surface is restructured. S3 is the architectural fix. S4 and S5
are hardening; they may ship in parallel after S3 is deployed.

| Slice | Name | Primary axis fixed | Restores audio? | Supersedes relief grants? |
|---|---|---|---|---|
| S2 | NullAudioConsumer sentinel | Fatal coupling | No (safety-net only) | No |
| S3 | Sole-provisioner (hub) | N-way control-plane | No (audio already restored by #1520) | Yes — 3 CREATE/UPDATE adapter grants |
| S4 | ACL grant-group schema v4 | Flat ACL copy-paste | No | No (is the structural fix for copy-paste) |
| S5 | CI falsification gate | Blind-spot in CI | No | — |

---

## S2 — NullAudioConsumer sentinel (ship first)

**Purpose.** Prevent future crash-loops where audio failure kills the text adapter.
Ships before S3 so the safety-net is in place before ACL surface is restructured.

### Files touched

| File | Change |
|---|---|
| `src/lyra/adapters/nats/jetstream_audio_consumer.py` | Add `NullAudioConsumer` class (no-op async `stop()`) |
| `src/lyra/bootstrap/standalone/audio_consumer_bootstrap.py` | Wrap body in `try/except Exception`; return `NullAudioConsumer` on failure; update return type annotation |
| `src/lyra/bootstrap/wiring/standalone_telegram.py` | No change to teardown — `consumer.stop()` already unconditional; update tuple type hint if needed |
| `src/lyra/bootstrap/wiring/standalone_discord.py` | Same as telegram |

### NullAudioConsumer placement

Place in `src/lyra/adapters/nats/jetstream_audio_consumer.py` alongside
`JetStreamAudioConsumer`. This keeps the sentinel co-located with the real consumer;
no new file.

```python
class NullAudioConsumer:
    """No-op sentinel returned by start_audio_consumer on audio-degraded boot.

    Satisfies the consumer interface so teardown calls .stop() unconditionally.
    No platform-axis if-guards needed — the sentinel collapses N guard sites to zero.
    """
    async def stop(self) -> None:
        pass
```

### Before → after: start_audio_consumer

Before:
```python
async def start_audio_consumer(js, platform, bot_id, adapter) -> JetStreamAudioConsumer:
    await ensure_stream(js)
    kv = await ensure_kv(js)
    # ... raises on any failure
```

After:
```python
async def start_audio_consumer(
    js, platform, bot_id, adapter
) -> JetStreamAudioConsumer | NullAudioConsumer:
    try:
        # ensure_stream / ensure_kv remain here until S3 moves them to hub
        await ensure_stream(js)
        kv = await ensure_kv(js)
        durable = f"outbound-audio-{platform}-{bot_id}"
        filter_subject = f"lyra.outbound.audio.{platform}.{bot_id}"
        await ensure_consumer(js, durable=durable, filter_subject=filter_subject)
        consumer = JetStreamAudioConsumer(
            js,
            durable=durable,
            filter_subject=filter_subject,
            send_audio=adapter.render_audio,
            send_text=adapter.send,
            dedup=KvSentSet(kv),
        )
        await consumer.start()
        log.info("audio_consumer_bootstrap: consumer started ...")
        return consumer
    except Exception:
        log.exception(
            "audio_consumer_bootstrap: audio consumer failed to start"
            " (platform=%s bot_id=%s) — audio degraded, text unaffected",
            platform,
            bot_id,
        )
        return NullAudioConsumer()
```

Note: `ensure_stream` and `ensure_kv` remain in this function until S3. S3 removes
them and replaces the `ensure_kv` call with `js.key_value(KV_BUCKET)` (bind-only).
S2 is purely additive — no ACL or provisioning change.

### Teardown verification

`_close_tg_wired` (line 55 of `standalone_telegram.py`):
```python
close_coros = [
    coro
    for a, ibus, tl, consumer in wired
    for coro in (a.close(), ibus.stop(), tl.stop(), consumer.stop())
]
```
`NullAudioConsumer.stop()` is a coroutine — `consumer.stop()` resolves to the no-op
coroutine, which `close_safely` awaits normally. No change required at this call site.

Verify same pattern holds for `_close_dc_wired` (line 88 of `standalone_discord.py`).

### Test plan

- Unit: `test_start_audio_consumer_returns_null_on_ensure_stream_failure` —
  mock `ensure_stream` to raise `nats.errors.Error`; assert return is
  `NullAudioConsumer` instance; assert log contains "audio degraded".
- Unit: `test_null_audio_consumer_stop_is_awaitable` — `await NullAudioConsumer().stop()`
  returns without exception.
- Integration smoke: adapter boot with NATS `$JS.API.STREAM.CREATE` denied → adapter
  serves text, does not raise.

### Verification

1. Deploy to staging with `ensure_stream` temporarily raising (mock via env flag or
   test credentials without STREAM.CREATE grant).
2. Confirm adapter starts, handles a text message, logs "audio degraded".
3. Confirm no crash-loop.
4. Revert mock; confirm full audio boot on next restart.

---

## S3 — Sole-provisioner: hub provisions stream + KV

**Purpose.** Move `ensure_stream` and `ensure_kv` from adapter bootstrap to hub
bootstrap. Remove stream control-plane grants from adapter ACL identities.

### Files touched

| File | Change |
|---|---|
| `src/lyra/bootstrap/standalone/hub_standalone.py` | Add `js = nc.jetstream()` + `await ensure_stream(js)` + `await ensure_kv(js)` before `announce_hub_ready(nc)` |
| `src/lyra/bootstrap/standalone/audio_consumer_bootstrap.py` | Remove `ensure_stream` + `ensure_kv` calls; replace `ensure_kv(js)` with `js.key_value(KV_BUCKET)` (bind-only); update ordering docstring |
| `src/lyra/infrastructure/outbound_audio/CLAUDE.md` | Update invariants: `ensure_stream` + `ensure_kv` are hub-owned; `start_audio_consumer` is bind-only for KV |
| `deploy/nats/acl-matrix.json` | Remove from `telegram-adapter` + `discord-adapter` publish: `STREAM.CREATE.LYRA_OUTBOUND_AUDIO`, `STREAM.UPDATE.LYRA_OUTBOUND_AUDIO`, `STREAM.CREATE.KV_lyra_outbound_audio_sent` |

### Before → after: hub_standalone.py (insertion point)

The hub already calls `nc.jetstream()` indirectly via internal stores or proxies.
Find the first available `js` handle or create one explicitly:

Before (around line 157):
```python
        await announce_hub_ready(nc)
```

After:
```python
        # Provision shared audio infrastructure before signalling readiness.
        # Adapters block on wait_for_hub; stream + KV are guaranteed to exist
        # when they connect. Idempotent: safe on every hub restart. ADR-079.
        from lyra.infrastructure.outbound_audio.stream_setup import (
            ensure_kv,
            ensure_stream,
        )
        _audio_js = nc.jetstream()
        await ensure_stream(_audio_js)
        await ensure_kv(_audio_js)

        await announce_hub_ready(nc)
```

### Before → after: audio_consumer_bootstrap.py ordering docstring

Before (lines 9–15):
```
    js = nc.jetstream()          -- called once per bootstrap_*_standalone
    await ensure_stream(js)
    kv = await ensure_kv(js)     -- idempotent; KvSentSet wraps the handle
    await ensure_consumer(...)
```

After:
```
    # Stream LYRA_OUTBOUND_AUDIO and KV lyra_outbound_audio_sent are provisioned
    # by the hub before announce_hub_ready (ADR-079 sole-provisioner). Adapters
    # are bind-only: js.key_value() binds the existing bucket; ensure_consumer()
    # creates the per-bot durable consumer. Do NOT call ensure_stream/ensure_kv here.
    kv = await js.key_value(KV_BUCKET)   -- bind-only; hub already provisioned
    await ensure_consumer(...)
```

### ACL change: acl-matrix.json

`telegram-adapter` and `discord-adapter` publish — remove exactly these three
subjects (control-plane CREATE/UPDATE moves to hub sole ownership):
```
"$JS.API.STREAM.CREATE.LYRA_OUTBOUND_AUDIO",
"$JS.API.STREAM.UPDATE.LYRA_OUTBOUND_AUDIO",
"$JS.API.STREAM.CREATE.KV_lyra_outbound_audio_sent",
```

Do NOT remove `$JS.API.STREAM.INFO.LYRA_OUTBOUND_AUDIO` — the adapter needs it for
`pull_subscribe`, which calls `stream_info` to validate stream existence.

Hub `publish` is unchanged (`$JS.API.>` already covers all provisioning calls).

After removing from adapters, verify via `lyra-acl genkeys --template-only` that the
rendered auth.conf for telegram-adapter and discord-adapter no longer contains the
three removed subjects, and still contains `STREAM.INFO.LYRA_OUTBOUND_AUDIO`.

### Provision-before-consume ordering guarantee

```
hub_standalone.py:
  ensure_stream(js)        ← S3 addition
  ensure_kv(js)            ← S3 addition
  announce_hub_ready(nc)   ← existing

standalone_telegram.py:
  wait_for_hub(nc)         ← existing (blocks until announce_hub_ready)
  start_audio_consumer(…)  ← ensure_consumer + key_value (bind)

standalone_discord.py:
  wait_for_hub(nc)         ← existing
  start_audio_consumer(…)  ← same
```

This ordering is load-bearing. Any future refactor that moves `announce_hub_ready`
earlier in the hub sequence must ensure `ensure_stream` + `ensure_kv` precede it.

### Unified-mode verification

The `lyra start` unified bootstrap runs hub + adapters in one process. The
provision-before-consume ordering must hold there too. Verify that the
unified bootstrap calls the hub wiring sequence before the adapter wiring sequence,
or that an equivalent barrier exists. If no barrier exists, add an explicit
`await ensure_stream(js)` + `await ensure_kv(js)` call in the unified bootstrap
before any adapter starts, and open a follow-up issue to document the unified-mode
ordering invariant.

### Test plan

- Integration: hub starts → adapter starts → `JetStreamAudioConsumer` starts without
  error; adapter identity does NOT hold STREAM.CREATE grant.
- Unit: `test_hub_provisions_audio_stream_before_ready` — mock `ensure_stream` +
  `ensure_kv`; assert both are called before `announce_hub_ready`.
- Regression: `test_adapter_acl_no_stream_create` — load rendered auth.conf from
  `--template-only`; assert `$JS.API.STREAM.CREATE.LYRA_OUTBOUND_AUDIO` is NOT in
  the telegram-adapter or discord-adapter user block.

### Verification

1. Deploy hub with S3. Confirm `ensure_stream` + `ensure_kv` log lines appear in
   `journalctl --user -u lyra-hub` before the `announce_hub_ready` log line.
2. Deploy adapters with S3 ACL (STREAM.CREATE removed). Confirm adapters start and
   `JetStreamAudioConsumer` binds successfully.
3. Confirm the three S3-removed grants are gone from the rendered auth.conf:
   `STREAM.CREATE.LYRA_OUTBOUND_AUDIO`, `STREAM.UPDATE.LYRA_OUTBOUND_AUDIO`,
   `STREAM.CREATE.KV_lyra_outbound_audio_sent`. Confirm `STREAM.INFO.LYRA_OUTBOUND_AUDIO`
   is still present (adapter needs it for `pull_subscribe`).

---

## S4 — ACL grant-group schema v4

**Purpose.** Eliminate copy-paste of audio grants between telegram-adapter and
discord-adapter. Define `audio-consumer` group once; reference it from each adapter.

### Files touched

| File | Change |
|---|---|
| `scripts/_acl_models.py` | Add `GroupDefinition(TypedDict)`; extend `LoadedMatrix` with `groups`; extend `Identity` with `groups` |
| `scripts/_loader.py` | Parse optional `"groups"` key in `load_matrix`; validate cross-references; bump `_VALID_VERSIONS` to include `"4"` |
| `scripts/_renderer.py` | Expand group subjects into effective `pub_allow`/`sub_allow` before flow injection in `render_auth_conf` |
| `deploy/nats/acl-matrix.json` | Bump version to `"4"`; add top-level `"groups"` key with `audio-consumer`; add `"groups": ["audio-consumer"]` to `telegram-adapter` and `discord-adapter`; remove the 8 publish + 1 subscribe subjects from each adapter that are now in the group |

### New types (_acl_models.py)

```python
class GroupDefinition(TypedDict):
    description: str
    publish: list[str]
    subscribe: list[str]

class LoadedMatrix(TypedDict):
    version: str
    identities: dict[str, Identity]
    request_reply_flows: NotRequired[list[Flow]]
    groups: NotRequired[dict[str, GroupDefinition]]

# Identity gains:
#   groups: NotRequired[list[str]]
```

### _renderer.py expansion logic (insertion in render_auth_conf)

```python
group_defs = matrix.get("groups", {})
for name, identity in identities.items():
    if identity["status"] == "retired":
        continue
    pub_allow[name] = list(identity.get("publish", []))
    sub_allow[name] = list(identity.get("subscribe", []))
    # Expand groups: union of identity subjects + all referenced group subjects
    for g in identity.get("groups", []):
        gdef = group_defs.get(g, {})
        for s in gdef.get("publish", []):
            if s not in pub_allow[name]:
                pub_allow[name].append(s)
        for s in gdef.get("subscribe", []):
            if s not in sub_allow[name]:
                sub_allow[name].append(s)
```

Group expansion runs before flow injection (the existing flow loop is unchanged).
Output order within the allow list must be deterministic: iteration is stable
(list insertion order preserved).

### acl-matrix.json subjects movement

All values derived directly from post-#1520 matrix (`deploy/nats/acl-matrix.json`).

Subjects moving FROM `telegram-adapter.publish` and `discord-adapter.publish`
TO `groups.audio-consumer.publish` (the remaining audio subjects post-S3 removal):
```
$JS.API.STREAM.INFO.LYRA_OUTBOUND_AUDIO
$JS.API.CONSUMER.CREATE.LYRA_OUTBOUND_AUDIO.>    ← .> not .*
$JS.API.CONSUMER.INFO.LYRA_OUTBOUND_AUDIO.*
$JS.API.CONSUMER.MSG.NEXT.LYRA_OUTBOUND_AUDIO.*
$JS.API.STREAM.INFO.KV_lyra_outbound_audio_sent
$JS.API.STREAM.MSG.GET.KV_lyra_outbound_audio_sent
$JS.ACK.LYRA_OUTBOUND_AUDIO.>
$KV.lyra_outbound_audio_sent.>
```

Subjects moving FROM `telegram-adapter.subscribe` and `discord-adapter.subscribe`
TO `groups.audio-consumer.subscribe`:
```
$KV.lyra_outbound_audio_sent.>
```

After S4, each adapter identity's `publish` and `subscribe` lists contain zero
audio-specific subjects directly — all are inherited via `"groups": ["audio-consumer"]`.

### Test plan

- Unit: `test_group_expansion_produces_identical_output` — load v3 matrix (subjects
  on identities directly); load v4 matrix (same subjects moved to group); render both;
  assert `parse_auth_conf(v3_output) == parse_auth_conf(v4_output)`.
- Unit: `test_loader_rejects_undefined_group_reference` — matrix with identity
  referencing `"groups": ["nonexistent"]`; assert `load_matrix` exits non-zero.
- Unit: `test_loader_accepts_v3_matrix_without_groups` — v3 matrix loads without error.
- Unit: `test_group_definition_publish_is_deterministic` — same subjects in different
  insertion order produce same rendered output (output is sorted or insertion-stable).

### Verification

```bash
# Before migration:
uv run lyra-acl genkeys --template-only > /tmp/before.conf

# Apply v4 schema change:
# (edit acl-matrix.json: version bump + add groups key + move subjects)

# After migration:
uv run lyra-acl genkeys --template-only > /tmp/after.conf

diff /tmp/before.conf /tmp/after.conf
# Expected: empty diff (auth.conf output is identical)
```

If diff is non-empty, the migration has a bug — fix before merging.

---

## S5 — CI falsification gate

**Purpose.** Close the blind spot where dev NATS (permissive) masks missing prod
ACL grants. Gate asserts code-required subjects ⊆ effective ACL before merge.

### Files touched

| File | Change |
|---|---|
| `deploy/nats/code-subjects.json` | New file — stream/KV-keyed manifest |
| `scripts/gen_nkeys.py` | New `check grants` sub-subcommand under `check` |
| `.github/workflows/ci.yml` | New CI step after `check flows` |

### code-subjects.json

Keyed by stream/KV resource (not by identity) to avoid N×adapter growth.
See ADR-079 Decision section (d) for the full JSON schema.

Initial entries at S5 ship time:
- `LYRA_OUTBOUND_AUDIO` stream — provisioner: hub; consumer-group: audio-consumer
- `LYRA_TURNS` stream — provisioner: turn-writer (no consumer-group; sole consumer)
- `lyra_outbound_audio_sent` KV — provisioner: hub; consumer-group: audio-consumer

### check grants logic (gen_nkeys.py)

New subcommand `lyra-acl check grants --matrix <path> --code-subjects <path>`:

```
For each stream in code-subjects.json:
  1. Look up provisioner identity in matrix.
  2. Resolve provisioner's effective publish list (post-group-expansion).
  3. For each subject in provisioner_subjects.publish:
       assert _subject_covered(subject, effective_publish)
       else: print FAIL + exit 1

  4. If consumer_group is set:
       Find all identities whose "groups" list contains consumer_group.
       For each such identity:
         Resolve effective publish + subscribe lists.
         For each subject in consumer_subjects.publish:
           assert _subject_covered(subject, effective_publish)
         For each subject in consumer_subjects.subscribe:
           assert _subject_covered(subject, effective_subscribe)
         else: print FAIL + exit 1

Same for kv_buckets.
```

Failure output format:
```
FAIL: identity 'telegram-adapter' publish[] does not cover required subject
      '$JS.API.CONSUMER.CREATE.LYRA_OUTBOUND_AUDIO.>'
      (via stream LYRA_OUTBOUND_AUDIO consumer-group audio-consumer)
```

### CI step (.github/workflows/ci.yml)

Add after the `check flows` step:
```yaml
- name: Check ACL code coverage (ADR-079)
  run: uv run lyra-acl check grants
       --matrix deploy/nats/acl-matrix.json
       --code-subjects deploy/nats/code-subjects.json
```

### Test plan

- Unit: `test_check_grants_passes_on_valid_matrix` — S4 matrix with audio-consumer
  group + hub provisioner; manifest matches; assert exit 0.
- Unit: `test_check_grants_fails_missing_provisioner_subject` — remove
  `STREAM.CREATE.LYRA_OUTBOUND_AUDIO` from hub publish; assert exit 1 + FAIL line.
- Unit: `test_check_grants_fails_missing_consumer_subject` — remove
  `CONSUMER.CREATE.LYRA_OUTBOUND_AUDIO.*` from audio-consumer group; assert exit 1.
- Unit: `test_check_grants_passes_identity_not_in_group` — identity without
  `audio-consumer` group is not checked for consumer subjects.

### Verification

1. CI pipeline green on S5 PR with valid matrix.
2. Introduce a deliberate missing grant (test branch): CI step fails with correct
   FAIL message, exit 1.
3. Confirm `lyra-acl check grants` runs in < 2 s locally (no network calls).

---

## Delivery order and gate conditions

```
S2  →  merge independently (safety-net)
             ↓
S3  →  requires S2 deployed (NullAudioConsumer must be live before ACL tightened)
             ↓
S4  →  requires S3 merged (group contains post-S3 adapter subject set; identity lists must be trimmed first)
S5  →  requires S4 merged (gate must resolve group definitions)
```

S4 and S5 may be developed in parallel; S5 cannot land before S4 (the check grants
logic depends on the group schema).

## Relief grant supersession map

All entries derived from post-#1520 `deploy/nats/acl-matrix.json`.

| Relief grant added by PR #1520 | Action by slice | Rationale |
|---|---|---|
| `$JS.API.STREAM.CREATE.LYRA_OUTBOUND_AUDIO` (adapters) | S3 removes | Hub sole-provisioner; adapters don't create the stream |
| `$JS.API.STREAM.UPDATE.LYRA_OUTBOUND_AUDIO` (adapters) | S3 removes | Same |
| `$JS.API.STREAM.CREATE.KV_lyra_outbound_audio_sent` (adapters) | S3 removes | Hub sole-provisioner; adapters don't create the KV bucket |
| `$JS.API.STREAM.INFO.LYRA_OUTBOUND_AUDIO` (adapters) | S4 moves to group | Stays on adapters (needed by `pull_subscribe`); deduped via group |
| `$JS.API.STREAM.INFO.KV_lyra_outbound_audio_sent` (adapters) | S4 moves to group | Stays on adapters (`key_value()` bind); deduped via group |
| `$JS.API.STREAM.MSG.GET.KV_lyra_outbound_audio_sent` (adapters) | S4 moves to group | Stays on adapters (`kv.get()`); deduped via group |
| `$JS.ACK.LYRA_OUTBOUND_AUDIO.>` (adapters) | S4 moves to group | Stays on adapters (`msg.ack()`/`term()`); deduped via group |
| `$JS.API.CONSUMER.CREATE.LYRA_OUTBOUND_AUDIO.>` (adapters, `.*` → `.>` fix) | S4 moves to group | Stays on adapters (`add_consumer`); `.>` wildcard correct; deduped |
| All other audio grants already in pre-#1520 matrix | S4 moves to group | Deduped; no functional change |

Grants marked "S4 moves to group": the rendered auth.conf subject is unchanged — the
subject moves from per-identity list to group definition, expanding identically.
Grants marked "S3 removes": the subject is no longer present in either adapter identity
after S3; the hub's `$JS.API.>` implicitly covers the provisioning operations.
