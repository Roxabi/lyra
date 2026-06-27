**Goal:** Harden the operator control-plane HTTP surface (`factory-dashboard`) before any exposure beyond Tailnet-only smoke.

## Context (reframed 2026-06-27 — ADR-094)

`factory-dashboard` (renamed from `factory-web`) is the **sole operator human surface** (#1760 / ADR-092), not a throwaway smoke harness. Today:

- Binds `${TAILSCALE_IPV4}:8765` — Tailnet membership = only boundary
- `platform=web`, `bot_id=smoke`, `user_id=smoke` in hub
- No session tokens, no per-operator identity, no CSRF

## Risk (unchanged, now production-critical)
- Any Tailnet peer can chat (LLM spend)
- Client-supplied `session_id` → cross-session read on SSE
- No audit trail tying actions to a real operator principal (ADR-090)

## Scope

### Phase 1 (current — acceptable for Tailnet-only)
- Document boundary in `deploy/AGENTS.md` ✅
- Fail-closed `ExecStartPre` on empty `TAILSCALE_IPV4` ✅

### Phase 2 (required before GA dashboard / non-Tailnet)
- [ ] Operator session auth on `factory-dashboard` (bearer cookie or OIDC — decide in spec)
- [ ] Real `user_id` on inbound messages (feeds ADR-090 `AuthorizeAgentMiddleware`)
- [ ] Session ownership on SSE streams (no cross-session read)
- [ ] Enables #1769 phase-2 platform rename (`dashboard.<bot>`)

## Out of scope
- Full ADR-090 CLI grants (separate epic #1980 post-MVP)
- LAN / `0.0.0.0` bind without strong auth

## Deps
- Blocks #1760 GA and #1769 phase-2 cutover
- Parent context: #1760, ADR-094

**Smoke mode** for CI/dev: pytest + optional `FACTORY_SMOKE_MODE` — not prod Quadlet posture.