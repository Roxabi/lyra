# src/lyra/inbound/ — Stage-Axis Inbound Pipeline

## Purpose

Stage-axis decomposition of the inbound message pipeline per Phase 3 of Epic #1277
(`artifacts/analyses/1277-stage-axis-refactor-strategy.mdx` §6).
Replaces duplicated routing/session logic spread across per-platform `*_inbound.py`
files with per-stage modules that `telegram_inbound.py` and `discord_inbound.py` both
delegate to.

## Pipeline shape

```
parse → AttachmentIngestStage (store-conditional; no-store path clears pending closures)
       → pre_route_hook(opt) → Router → [DROP|PROCESS]
       → pre_session_hook(opt) → SessionBuilder → Dispatcher
```

- `AttachmentIngestStage` — central inbound attachment ingest (ADR-083, epic #1537). Runs immediately after parse, **before** routing. When a store is configured, uploads binary attachments on the `InboundMessage` to `BlobStore`, replacing raw bytes with `BlobRef` values so downstream stages (dispatcher, agents) receive opaque refs only. Failures degrade gracefully (`blob_ref=None`). When no store is configured, the stage is skipped; instead, any `pending_attachment` (singular, audio #1551) and `pending_attachments` (plural, non-audio #1552) closures are cleared via `dataclasses.replace` — a closure must never cross the NATS process boundary (transport-boundary invariant, ADR-083).
- `pre_route_hook` — Discord cold-path: mutates `RouterCtx.owned_threads` with lazy `is_owned` lookup so Router stays sync + pure. Telegram supplies neither hook — its routing is fully determined by PlatformMeta, and Telegram has no thread model.
- `pre_session_hook` — Discord auto-thread create + claim. Returns updated `InboundMessage` (via `dataclasses.replace`) so downstream stages see the resolved `DiscordMeta.thread_id`. Discord supplies both hooks (pre_route for cold-path is_owned warmup; pre_session for auto-thread create + claim).
- On `RouteDecision.DROP` the pipeline returns immediately; `on_drop` callback fires (e.g. cancel typing).

## Layer invariants

- `router.py`, `session_builder.py`, `dispatcher.py`, `pipeline.py` must NOT import `discord` or `aiogram`. Platform isolation lives in `wire_parser_telegram.py` / `wire_parser_discord.py` and adapter-side hooks.
- Stages depend on `lyra.core` only — never `lyra.adapters`. Enforced by `.importlinter` (`inbound-no-adapters` contract, #1287). Known violations are tagged `DEBT:inbound-adapters-transition` / `DEBT:inbound-adapters-wireparser` and listed as `ignore_imports` in that contract pending relocation to `lyra.core`/lyra.shared (deferred to #1283 Phase 6).
- `InboundContext`, `RouterCtx`, `SessionCtx`, `DispatchCtx` are `@dataclass(frozen=True)`. The container references are frozen; the mutable collections they carry (`RouterCtx.owned_threads: set`, `SessionCtx.thread_sessions_cache: dict`) are mutated in place by hooks and `SessionBuilder`. Document this contract on `context.py` module docstring.
- Hook signatures (verified post-Phase-3):
  - `pre_route_hook(InboundMessage, InboundContext) -> Awaitable[None]` — may mutate
    `RouterCtx.owned_threads`. Never returns a value; the pipeline continues with the same
    msg. Discord supplies this for cold-path `ThreadStore.is_owned` warmup.
  - `pre_session_hook(InboundMessage, InboundContext) -> Awaitable[InboundMessage]` — MUST
    return an `InboundMessage` (typically via `dataclasses.replace`). Discord supplies this
    for auto-thread creation + claim; the returned msg carries the resolved
    `DiscordMeta.thread_id` so `SessionBuilder` + `Dispatcher` address the right thread.
- Adapter-side hooks live in `src/lyra/adapters/{platform}/{platform}_inbound.py` because
  they need platform-library API calls (`discord.Thread`, etc.). They are bound to the
  pipeline via `functools.partial`.
- `Router.decide` is sync and pure — must NOT perform I/O or raise exceptions; return `RouteDecision.DROP` instead.
- `send_backpressure` is a per-call argument on `Dispatcher.dispatch`, not stored in `DispatchCtx` — its closure captures the raw platform message which is known only at call time.

## What NOT to do

- ¬ add platform-specific code (`discord.*`, `aiogram.*`) to generic stage modules — extend `wire_parser_<platform>.py` or the adapter-side hook instead.
- ¬ raise exceptions from `Router.decide` — return `RouteDecision.DROP`.
- ¬ mutate the frozen dataclass container itself; only the mutable collections it carries.
- ¬ move Discord thread API helpers (`persist_thread_claim`, `persist_thread_session`, `retrieve_thread_session`) here — they import `discord` and stay in `adapters/discord/discord_threads.py`.
- ¬ store `send_backpressure` in `DispatchCtx`. The closure captures the raw platform
  message, which is only known per-call. Pass it as a direct argument to
  `Dispatcher.dispatch` / `InboundPipeline.run`.

## DEBT carry-over

Phase 3 audit (2026-05-20): No `DEBT:boundary-broad-catch` residuals after Phase 3 audit.

Both BLE001 sites from the original `discord_inbound.py` were drained:

- `discord_inbound.py` pre-route hook (`ThreadStore.is_owned`) — narrowed from `except Exception:` to `except sqlite3.Error:` (stdlib, no new dep; aiosqlite wraps sqlite3 at the driver level and the ThreadStore Protocol does not declare a narrower exception type).
- `discord_inbound.py` pre-session hook recovery path (`persist_thread_claim`) — outer `except Exception as e` removed as dead code: `persist_thread_claim` in `discord_threads.py` already catches and logs all exceptions internally without re-raising.

No `DEBT:boundary-broad-catch` annotations remain in `src/lyra/adapters/{telegram,discord}/*_inbound.py` or `src/lyra/inbound/*.py`.

## Epic #1277 DEBT slug correction

Epic #1277 § 8 lists `adapter-dispatch-complexity` and `adapter-magic-constants` as drained by Phase 3. Verification (2026-05-20): these slugs do not appear in any inbound file (`grep -rn 'adapter-dispatch-complexity\|adapter-magic-constants' src/lyra/adapters/{telegram,discord}/*_inbound.py` returns 0 hits). They belong to outbound + audio + formatting code paths. The corrected target is documented in `artifacts/specs/1280-phase-3-inbound-stages-spec.mdx`.
