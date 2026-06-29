# Runbook — Persona / soul operator guide

How soul edits propagate (or do not) across harnesses.

## Soul is session-scoped

| Action | `Agent.config.system_prompt` | Active session / pool |
|--------|------------------------------|------------------------|
| Save soul in dashboard | Updates on next hub load (ADR-029) | **Unchanged** until new pool |
| `factory agent patch` persona | Same | Same |
| `/reset` or new chat tab | N/A | New session gets current soul |

**Expected lag** — not a defect. Tell operators: after editing soul, start a **new conversation** or `/reset` for immediate effect.

## Edit paths (V1)

| Path | Flow |
|------|------|
| Dashboard `/agents/:name` | PATCH scalars + PUT `soul.md` via BFF → hub `soul.put` |
| CLI scalars | `factory agent patch <name> --json '{"model":"…"}'` — harness/model/voice scalars only |
| CLI soul doc | `scripts/backfill_soul_documents.py` (migration) or hub NATS `soul.put` via RPC client — no direct `factory agent patch` on soul markdown V1 |
| Legacy | `persona_json` inline — fallback until column dropped |

Compose happens **only** in `core/persona.py` on the hub — never in the SPA or harness workers.

## Harness behaviour

| Harness | Apply point |
|---------|-------------|
| **claude-cli** | Subprocess spawn `--system-prompt-file`; respawn if prompt changes |
| **omp-rpc** | Session acquire / `set_system_prompt` at cold session (V2 parity) |

Both receive opaque `system_prompt: str` in `JobEnvelope.payload` — never `persona_json` or raw blob bytes.

## Limits

| Gate | Limit |
|------|-------|
| `soul.md` raw document | 48 KiB (`MAX_SOUL_DOCUMENT_BYTES`) |
| Composed prompt | 64 KiB (`MAX_PROMPT_BYTES`) |

Save rejected over limit — trim sections or split expertise/guidelines.

## Dashboard chat defaults

New chat tabs load `backend` + `model` from agent DB row (not hardcoded `claude-cli` / `sonnet`). Per-tab overrides in `localStorage` still apply; indicator shows when override ≠ DB default.

## OMP vs clipool checklist

1. Edit soul for agent with `backend=omp-rpc`
2. Open **new** dashboard chat (or `/reset` on TG/DC)
3. Confirm OMP session receives same composed string as clipool agent with identical soul (grep hub logs / job envelope `system_prompt` hash if needed)

## Memory (V1)

`soul_meta_json.memory.enabled` is provision-only — no `set_memory()` or vault identity anchor in this release. Memory line hidden in dashboard UI.

## Security / threat model (#1992)

Soul editor is **control-plane** access: anyone who can reach the dashboard BFF can mutate agent identity for all bots bound to that agent. V1 assumes **Tailnet-only** reachability (no public ingress). Dashboard BFF has no per-operator OIDC yet (#1992) — restrict Tailnet membership, audit `soul.put` logs, run secret lint before save. Do not embed API keys in soul markdown.