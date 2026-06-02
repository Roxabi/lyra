# src/factory/transport/ — NATS Transport + Worker Pool

## Purpose

Domain-agnostic NATS transport primitives consumed by all domain worker clients
(`lyra.nats.*_client`, `factory.llm.llm_client`). Lives here, NOT in `packages/roxabi-nats/`.

## Layer contract

```
NatsTransport          — call() + open_inbox() CM; owns NATS inbox lifecycle
WorkerPoolClient       — routing + CB + heartbeat subscription; registry via DI
DomainClient           — thin wrapper in lyra.nats / lyra.llm (compose pool + codec)
```

## Key invariants

- `NatsTransport.open_inbox()` returns an `AsyncGenerator[InboxStream, None]`; it is consumed with `async for`, not `async with`.
- `WorkerPoolClient` accepts a `WorkerRegistry` via dependency injection (bootstrap/factory
  owns the instance) and manages the heartbeat subscription. Domain clients MUST NOT
  subscribe to heartbeat subjects or open inboxes directly.
- `Result[T]` / `Ok[T]` / `Err` are the return types for all transport-level calls.
  `SanitizedError` strips internal detail before propagation to users (#1212).
- CB lives in `WorkerPoolClient` — domain clients must NOT add a second CB layer.

## SanitizedError.from_message

Scrubs user-safe soft-error wire text (e.g. upstream model errors) before bus propagation. Callers do NOT embed raw exception text — they pass a pre-selected message string (or empty).

Sanitization rules:
- empty input → `message = "model_error"` (fallback)
- control chars → replaced with space (`c.isprintable()` guard)
- truncated to 200 chars (`_BUS_MESSAGE_MAX_LEN`); suffix `…` if cut

Security boundary semantics UNCHANGED: `SanitizedError.message` still never carries `str(exc)` — `from_message` accepts only callee-controlled strings and applies guardrails on top.

## WorkScope (#1393)

`WorkScope` is a frozen dataclass whose `platform` and `bot_id` fields are
interpolated directly into NATS subjects (e.g. `lyra.typing.{platform}.{bot_id}`).
Both MUST match `^[A-Za-z0-9_-]{1,48}$`; `trace_id` MUST match
`^[A-Za-z0-9_-]{1,128}$`. Enforced in `__post_init__` — `ValueError` on violation.
Callers MUST NOT catch-and-ignore: a violation indicates a bug or an inbound
attack and the publish must abort, not silently downgrade.

`scope_id` is `int` and is not subject-interpolated; not validated here.

## TurnPublisher (#1331)

`TurnPublisher` (transport-level) publishes `TurnWriteEvent` to JetStream
subject `lyra.turns.write`, awaiting PubAck before returning. Used by
pool/observer/inbound rewire (#1331) to replace direct TurnStore mutator
calls. Required `trace_id: str` per call (non-empty; threaded from
inbound msg.id when available, else uuid4().hex).

Consumer: `factory.infrastructure.turn_writer.TurnWriter` (sole writer).
