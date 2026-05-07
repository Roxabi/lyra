# Handoff brief — lyra#1104

> Drop into a fresh Claude session at `/home/mickael/projects/lyra` and run `/dev #1104`. This file is the context dump.

## What this is

Sister PR for **Roxabi/llmCLI#12 / PR #18** — bundled coordinated merge.
- llmCLI side (Roxabi/llmCLI#18): worker speaks canonical Pydantic envelopes via `roxabi_contracts.llm`, posts to LiteLLM, ready (DRAFT, label `reviewed`).
- lyra side (this issue, #1104): the **lyra-side prerequisites** that make the canonical wire actually work end-to-end.

Without lyra#1104, the llmCLI worker subscribes a subject the deployed broker ACL doesn't allow → no traffic flows → bundle stays red.

## Scope (4 buckets)

### 1. ACL — full canonical migration
Source of truth: **`deploy/nats/acl-matrix.json`** (NOT the generated `auth.conf`).

Edits required:

**A. Top-level `request_reply_flows[]`** — change the LLM flow's subject:
```json
{ "requester": "hub", "responder": "llm-worker", "subject": "lyra.llm.generate.request" }
```
(was `"lyra.llm.request"`)

**B. `identities.hub.publish[]`** — change `"lyra.llm.request"` → `"lyra.llm.generate.request"`.

**C. `identities.hub.subscribe[]`** — change `"lyra.llm.health.*"` → `"lyra.llm.heartbeat"`.

**D. `identities.llm-worker.subscribe[]`** — change `"lyra.llm.request"` → `"lyra.llm.generate.request"`.

**E. `identities.llm-worker.publish[]`** — change `"lyra.llm.health.*"` → `"lyra.llm.heartbeat"`.

Then regenerate `deploy/nats/auth.conf`:
```bash
sudo env "PATH=$PATH" lyra-acl genkeys --regenerate
```

Validation gates that must stay green:
- `bash scripts/check-acl-matrix-spec.sh`
- `bash scripts/check-acl-authconf-drift.sh`
- `python scripts/check_acl_matrix_retired.py`
- `bash tools/check-nats-acls.sh` (CI gate)

### 2. Hub bootstrap — switch from `NatsLlmDriver` to `NatsLlmClient`
The canonical client `src/lyra/nats/nats_llm_client.py` already exists but is NOT wired. Wire it in 5 files:

| File | Edit |
|---|---|
| `src/lyra/bootstrap/factory/llm_overlay.py` | `init_nats_llm()` → instantiate `NatsLlmClient` instead of `NatsLlmDriver` |
| `src/lyra/bootstrap/factory/wiring_helpers.py` | Update type annotations `NatsLlmDriver \| None` → `NatsLlmClient \| None` |
| `src/lyra/bootstrap/factory/hub_builder.py` | Adjust callsites — client uses async-iterator streaming `async for delta in client.stream(...)` and Pydantic envelopes (not driver's ad-hoc JSON callback) |
| `src/lyra/bootstrap/factory/agent_factory.py` | Same as `hub_builder.py` |
| `src/lyra/bootstrap/standalone/hub_standalone_helpers.py` | Same swap |

`NatsLlmClient` already publishes to `SUBJECTS.generate_request` and subscribes `SUBJECTS.heartbeat` — once §1 lands, it's operational without further code changes there.

### 3. Deprecate legacy `NatsLlmDriver`
- `src/lyra/llm/drivers/nats_driver.py` → add `# DEPRECATED — replaced by lyra.nats.nats_llm_client.NatsLlmClient (issue #N follow-up)` notice atop.
- Open a follow-up issue for full removal once consumers migrate + llmCLI #12 deploys.

### 4. Tests
- Update / rename `tests/llm/drivers/test_nats_driver.py` → exercise `NatsLlmClient` envelope path. (`tests/llm/drivers/test_cli_nats_driver.py` may also need updates if it touches the driver's request shape.)
- Integration: assert hub publishes on canonical request subject, subscribes canonical heartbeat, parses `LlmChunkEvent` / `LlmResponse` Pydantic replies.
- ACL regression: assert `auth.conf` post-regen contains the new canonical subjects and **does not** contain `lyra.llm.request` (legacy) or `lyra.llm.health.*`.

## Acceptance criteria (from issue body)
- [ ] `auth.conf` (regenerated) — hub: `publish: lyra.llm.generate.request` + `subscribe: lyra.llm.heartbeat`
- [ ] `auth.conf` — llm-worker: `subscribe: lyra.llm.generate.request` + `publish: lyra.llm.heartbeat`
- [ ] `auth.conf` — no `lyra.llm.request` (legacy) and no `lyra.llm.health.*`
- [ ] Hub bootstrap wires `NatsLlmClient` in place of `NatsLlmDriver` across the 5 files
- [ ] `NatsLlmDriver` annotated DEPRECATED with link to follow-up removal issue
- [ ] Existing hub LLM tests pass (migrated where needed)
- [ ] Coordinated merge with Roxabi/llmCLI#18 — both ready, bundle smoke test gates the window

## Coordination — bundled merge
- llmCLI PR #18 is DRAFT with `reviewed` label, all blockers + warnings resolved
- This PR opens DRAFT → review → ready → both merge in same window
- M₁ smoke test per `Roxabi/llmCLI:tests/nats/SMOKE.md` is the merge gate

## Out of scope (for this PR)
- Per-worker score-routed subjects `lyra.llm.generate.request.{worker_id}` (separate issue when fan-out across hosts is needed)
- ADR-045 `roxabi-nats` SDK uv-workspace extraction — orthogonal
- Full removal of `NatsLlmDriver` — follow-up after consumer migration
- LiteLLM proxy work — owned by llmCLI #12

## Tier
**F-lite** — clear scope, single domain (lyra Python backend + ACL config), no new architecture. ~7-10 files.

## Recommended path in fresh session
```bash
cd /home/mickael/projects/lyra
# (Open a fresh Claude Code session here)
/dev #1104
```

`/dev` will read this brief from `artifacts/frames/1104-canonical-llm-wire-handoff.md` (or you can pass it via `/frame`).

## Cross-references
- Issue: https://github.com/Roxabi/lyra/issues/1104
- Sister PR: https://github.com/Roxabi/llmCLI/pull/18
- Spec on llmCLI side: `Roxabi/llmCLI:artifacts/specs/12-nats-worker-spec.mdx`
- Plan on llmCLI side: `Roxabi/llmCLI:artifacts/plans/12-nats-worker-plan.mdx`
- Smoke test: `Roxabi/llmCLI:tests/nats/SMOKE.md`
- Follow-up: `Roxabi/lyra#1107` (add `worker.capacity` to canonical error vocabulary)

## Done state
PR ready + reviewed + coordinated merge with llmCLI#18. Smoke test on M₁ green for both stream and non-stream paths.
