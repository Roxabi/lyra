# Spec #992 — Topology-first ACL schema: request_reply_flows + derived inbox grants

## Problem

`acl-matrix.json` models authorization at the subject level. It cannot express
"hub talks to clipool via request-reply" as a concept. Every cross-identity
inbox dependency is manually maintained in both sides of the flow, making
missing-grant and case-mismatch bugs structurally possible.

Root cause from postmortem: the wrong abstraction level was chosen as SSoT.

---

## Goal

`request_reply_flows` becomes the authoritative declaration of which identities
participate in request-reply. The generator derives all `_inbox.X.>` entries
from those declarations — no manual inbox entries in identity blocks.

**What becomes structurally impossible after this change:**
- Case mismatch between connect-site `inbox_prefix` and ACL subscribe entry
- Missing publish grant on responder when a flow is added
- `allow_responses` silently becoming the sole auth path after ACL narrowing

---

## Schema change — `acl-matrix.json`

Add a top-level `request_reply_flows` array alongside `identities`:

```json
{
  "version": "1",
  "request_reply_flows": [
    { "requester": "hub", "responder": "clipool-worker", "subject": "lyra.clipool.cmd" },
    { "requester": "hub", "responder": "voice-tts",      "subject": "lyra.voice.tts.request.>" },
    { "requester": "hub", "responder": "voice-stt",      "subject": "lyra.voice.stt.request.>" },
    { "requester": "hub", "responder": "llm-worker",     "subject": "lyra.llm.request" },
    { "requester": "hub", "responder": "image-worker",   "subject": "lyra.image.generate.request" }
  ],
  "identities": { ... }
}
```

**Derivation rule** — for each flow `{requester, responder, subject}`:
- `requester.subscribe += ["_inbox.{requester}.>"]`
- `responder.publish  += ["_inbox.{requester}.>"]`

**Remove from identity blocks** (now derived):
- `hub.subscribe`: remove `"_inbox.hub.>"`
- `clipool-worker.publish`: remove `"_inbox.hub.>"`
- `voice-tts.publish`: remove `"_inbox.hub.>"`
- `voice-stt.publish`: remove `"_inbox.hub.>"`
- `llm-worker.publish`: remove `"_inbox.hub.>"`
- `image-worker.publish`: remove `"_inbox.hub.>"`

**Invariant:** `_inbox.X.>` entries must not appear in identity blocks when a
flow declaration covers them. The generator deduplicates — if somehow the same
entry appears both statically and derived, only one instance is emitted.

---

## Generator change — `gen-nkeys.sh`

Extend `load_matrix()` (lines 44-103) to process `request_reply_flows` after
the per-identity arrays are populated.

```bash
# Inside load_matrix(), after the identity loop:
while IFS= read -r flow; do
    requester=$(jq -r '.requester' <<<"$flow")
    responder=$(jq -r '.responder'  <<<"$flow")
    inbox="\"_inbox.${requester}.>\""

    # Inject into requester subscribe if not already present
    if [[ "${SUB_ALLOW[$requester]}" != *"${inbox}"* ]]; then
        if [[ -n "${SUB_ALLOW[$requester]}" ]]; then
            SUB_ALLOW[$requester]="${SUB_ALLOW[$requester]},${inbox}"
        else
            SUB_ALLOW[$requester]="${inbox}"
        fi
    fi

    # Inject into responder publish if not already present
    if [[ "${PUB_ALLOW[$responder]}" != *"${inbox}"* ]]; then
        if [[ -n "${PUB_ALLOW[$responder]}" ]]; then
            PUB_ALLOW[$responder]="${PUB_ALLOW[$responder]},${inbox}"
        else
            PUB_ALLOW[$responder]="${inbox}"
        fi
    fi
done < <(jq -c '.request_reply_flows[]?' "${MATRIX_JSON}")
```

All existing render paths (`--regen-authconf`, `--template-only`,
`--emit-merged-authconf`, default) automatically pick up derived entries
because they all read from `PUB_ALLOW`/`SUB_ALLOW` after `load_matrix()`.

---

## Files changed

| File | Change |
|---|---|
| `deploy/nats/acl-matrix.json` | Add `request_reply_flows`; remove manual `_inbox.hub.>` from identity blocks |
| `deploy/nats/gen-nkeys.sh` | Extend `load_matrix()` with flow derivation loop (~15 lines) |
| `docs/ops/nats-acl-inbox-case-postmortem.md` | Mark Phase 3 action items done |

---

## Acceptance criteria

1. `acl-matrix.json` has a `request_reply_flows` array covering all 5 current hub→worker flows
2. No `_inbox.hub.>` entry appears in any identity block's publish/subscribe arrays
3. `gen-nkeys.sh --template-only` output contains `"_inbox.hub.>"` in hub's subscribe and in each responder's publish
4. `gen-nkeys.sh --regen-authconf` succeeds and produces an `auth.conf` equivalent to the current one
5. All services reconnect after NATS reload with no `Permissions Violation` in logs

---

## Out of scope

- `nats_connect()` changes — already normalized to lowercase in Fix 1
- Fix 3 (`make test-acl` CI gate) — skipped
- Multi-requester topology — hub-spoke only
- `allow_responses` removal — keep as defence-in-depth for now

---

## References

- Issue: #992
- Postmortem: `docs/ops/nats-acl-inbox-case-postmortem.md` — Phase 3
- `deploy/nats/acl-matrix.json` — current ACL SSoT
- `deploy/nats/gen-nkeys.sh` lines 44-103 (`load_matrix`) — integration point
