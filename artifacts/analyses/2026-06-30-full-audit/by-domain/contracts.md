# Domain: contracts — roxabi-contracts package & cross-service wire discipline

Audit: 2026-06-30-full-audit · finder: contracts-wire · 2 findings (1 REFUTED already removed upstream — a prior `contracts-bump.yml` auto-merge-gate finding did not survive verification and is excluded from this pass)

Spot-check performed during synthesis (read source directly, not re-trusted from finder evidence alone):
- `packages/roxabi-contracts/pyproject.toml:3` → `version = "0.13.0"` confirmed.
- `packages/roxabi-contracts/CHANGELOG.md` → newest heading is still `## [0.11.0] (2026-06-11)`; grep for `0.12.0`/`0.13.0` returns zero hits anywhere in the file — confirmed.
- `git log --since="7 days ago" -- packages/roxabi-contracts/pyproject.toml` shows two bump commits this week landing after the 0.11.0 CHANGELOG entry (`0cb91264` voice-lifecycle, `aa2e79e7` fleet domain) — consistent with the finder's claim of two undocumented bumps.
- `packages/roxabi-contracts/src/roxabi_contracts/state/bot_roster.py` read in full — `RosterBotEntry` (line 28), `TelegramRosterBot` (line 41), `DiscordRosterBot` (line 52) all set `model_config = ConfigDict(extra="forbid", frozen=True)`; `public_bot: str | None = None` confirmed present on all three (lines 32/45/56). Module docstring already states the rationale: "auth fields (`owner_users`, grants) must never appear on the wire. `extra='forbid'` on entry models enforces this at parse time" — this is a pre-existing, intentional security boundary, not an oversight. Cross-repo grep confirms these models are consumed ONLY by `src/factory/infrastructure/kv/bot_roster.py` and `src/factory/bootstrap/wiring/kv_bot_roster.py` (intra-monorepo, co-deployed) — no external satellite currently imports them, corroborating the finder's own "currently low-risk" framing.

## P0 — Critical

None.

## P1 — High

None.

## P2 — Medium

None.

## P3 — Low

| ID | File:Line | Verdict | Title | Evidence | Recommendation |
|---|---|---|---|---|---|
| contracts-wire-F1 | `packages/roxabi-contracts/CHANGELOG.md:3` | UNVERIFIED (synthesis spot-check: facts confirmed) | CHANGELOG.md undocumented across two minor version bumps this week (0.11.0→0.13.0) | `pyproject.toml` at 0.13.0; CHANGELOG newest heading still `## [0.11.0]`. Two bumps (`0cb91264` voice-lifecycle, `aa2e79e7` fleet observability) landed with no CHANGELOG entry, breaking the package's own established discipline — every prior 0.4.0–0.11.0 bump documented additive-vs-breaking classification and any transitional shim with its flip-issue number (e.g. the `job_id` #1841 shim noted under 0.9.0). New `LyraEvent.tenant` additive field, the `fleet/` domain, and the voice-lifecycle domain are all unrecorded. | Backfill CHANGELOG.md entries for 0.12.0/0.13.0 documenting the additive nature of `tenant`/`sample_id`/`image_digest_status`/`public_bot` and the new fleet+voice-lifecycle domains; consider gating contracts version bumps on a CHANGELOG diff in CI so reviewers (and satellite maintainers who only see `contracts-bump.yml`'s auto-generated PR body) retain a human-readable trail. |
| contracts-wire-F3 | `packages/roxabi-contracts/src/roxabi_contracts/state/bot_roster.py:28` | UNVERIFIED (synthesis spot-check: facts confirmed; risk scope narrower than framed) | `RosterBotEntry`/`TelegramRosterBot`/`DiscordRosterBot` use `extra="forbid"` (opposite of `ContractEnvelope`'s forward-compat default `extra="ignore"`), and all three gained a field (`public_bot`) this week | `bot_roster.py:28/41/52` set `extra="forbid", frozen=True` — opposite of `ContractEnvelope`'s documented forward-compat invariant (`envelope.py:45-55`, restated in `packages/roxabi-contracts/AGENTS.md`). Commit `04c0ab2b` (this week) added `public_bot: str \| None = None` to all three models (lines 32/45/56). A consumer pinned to a pre-`04c0ab2b` version parsing a `roster.<platform>` KV doc written by a newer hub would raise a pydantic `ValidationError` on the unrecognized key — these models are silently NOT eligible for the package's additive-only minor-bump guarantee, even though nothing outside the module's own docstring signals that exception. **Mitigating context found during synthesis**: these are plain `BaseModel` subclasses, not `ContractEnvelope` subclasses, so the package-level forward-compat rule doesn't formally bind them; the docstring already documents the `extra="forbid"` choice as an intentional security boundary (auth fields must never leak onto the wire); and grep confirms zero external-satellite consumers today — risk is hypothetical, not live. | Either switch to `extra="ignore"` now that auth-sensitive fields (`owner_users`, grants) are kept out of these models by construction per the existing docstring rationale, or explicitly document in `roxabi-contracts/AGENTS.md` that `state/bot_roster.py` is intentionally exempt from the additive-only minor-bump guarantee, so future contributors don't assume bot_roster fields are wire-safe to add without coordinating consumers. |

## Duplications

None — the two surviving findings target distinct files/root causes (CHANGELOG discipline vs. model-config policy) with no overlapping evidence; no intra-domain merge needed. Not the ssot/axial domain, so no cross-domain duplication table is required here. Flagging for the reduce step: F1's "CHANGELOG is the only human-readable trail `contracts-bump.yml` doesn't auto-generate" pairs with any ci/cd-domain findings about `contracts-bump.yml`'s auto-merge path having thin gating — both point at the same workflow's lack of human-context generation, worth a single cross-domain narrative if another finder also surfaced it.
