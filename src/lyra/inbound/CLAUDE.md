# src/lyra/inbound/ — Stage-Axis Inbound Pipeline

## Purpose

Stage-axis decomposition of the inbound message pipeline per Phase 3 of Epic #1277
(`artifacts/analyses/1277-stage-axis-refactor-strategy.mdx` §6).
Replaces duplicated routing/session logic spread across per-platform `*_inbound.py`
files with per-stage modules that `telegram_inbound.py` and `discord_inbound.py` both
delegate to.

## Pipeline shape

```
parse → pre_route_hook(opt) → Router → [DROP|PROCESS] → pre_session_hook(opt) → SessionBuilder → Dispatcher
```

- `pre_route_hook` — Discord cold-path: mutates `RouterCtx.owned_threads` with lazy `is_owned` lookup so Router stays sync + pure. Telegram supplies no hook.
- `pre_session_hook` — Discord auto-thread create + claim. Returns updated `InboundMessage` (via `dataclasses.replace`) so downstream stages see the resolved `DiscordMeta.thread_id`.
- On `RouteDecision.DROP` the pipeline returns immediately; `on_drop` callback fires (e.g. cancel typing).

## Layer invariants

- `router.py`, `session_builder.py`, `dispatcher.py`, `pipeline.py` must NOT import `discord` or `aiogram`. Platform isolation lives in `wire_parser_telegram.py` / `wire_parser_discord.py` and adapter-side hooks.
- Stages depend on `lyra.core` only — never `lyra.adapters`. Enforced by importlinter.
- `InboundContext`, `RouterCtx`, `SessionCtx`, `DispatchCtx` are `@dataclass(frozen=True)`. The container references are frozen; the mutable collections they carry (`RouterCtx.owned_threads: set`, `SessionCtx.thread_sessions_cache: dict`) are mutated in place by hooks and `SessionBuilder`. Document this contract on `context.py` module docstring.
- Hook signatures:
  - `pre_route_hook(msg: InboundMessage, ctx: InboundContext) -> Awaitable[None]` — may mutate `RouterCtx.owned_threads`; must NOT replace the context itself.
  - `pre_session_hook(msg: InboundMessage, ctx: InboundContext) -> Awaitable[InboundMessage]` — must return an updated `InboundMessage` (typically via `dataclasses.replace`).
- `Router.decide` is sync and pure — must NOT perform I/O or raise exceptions; return `RouteDecision.DROP` instead.
- `send_backpressure` is a per-call argument on `Dispatcher.dispatch`, not stored in `DispatchCtx` — its closure captures the raw platform message which is known only at call time.

## What NOT to do

- ¬ add platform-specific code (`discord.*`, `aiogram.*`) to generic stage modules — extend `wire_parser_<platform>.py` or the adapter-side hook instead.
- ¬ raise exceptions from `Router.decide` — return `RouteDecision.DROP`.
- ¬ mutate the frozen dataclass container itself; only the mutable collections it carries.
- ¬ move Discord thread API helpers (`persist_thread_claim`, `persist_thread_session`, `retrieve_thread_session`) here — they import `discord` and stay in `adapters/discord/discord_threads.py`.
