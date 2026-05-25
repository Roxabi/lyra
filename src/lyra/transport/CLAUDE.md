# src/lyra/transport/ — NATS Transport + Worker Pool

## Purpose

Domain-agnostic NATS transport primitives consumed by all domain worker clients
(`lyra.nats.*_client`, `lyra.llm.llm_client`). Lives here, NOT in `packages/roxabi-nats/`.

## Layer contract

```
NatsTransport          — call() + open_inbox() CM; owns NATS inbox lifecycle
WorkerPoolClient       — routing + CB + heartbeat subscription; registry via DI
DomainClient           — thin wrapper in lyra.nats / lyra.llm (compose pool + codec)
```

## Key invariants

- `NatsTransport.open_inbox()` is an async context manager; the `InboxStream` it yields
  is only valid inside the `async with` block — never escape the CM.
- `WorkerPoolClient` accepts a `WorkerRegistry` via dependency injection (bootstrap/factory
  owns the instance) and manages the heartbeat subscription. Domain clients MUST NOT
  subscribe to heartbeat subjects or open inboxes directly.
- `Result[T]` / `Ok[T]` / `Err` are the return types for all transport-level calls.
  `SanitizedError` strips internal detail before propagation to users (#1212).
- CB lives in `WorkerPoolClient` — domain clients must NOT add a second CB layer.

## SanitizedError.from_message — Phase 5 addendum (#1282)

`from_message` was added in Phase 5 (#1282) as a Phase 1 addendum to the transport boundary.

Purpose: scrub user-safe soft-error wire text (e.g. upstream model errors) before bus propagation. Callers do NOT embed raw exception text — they pass a pre-selected message string (or empty).

Sanitization rules:
- empty input → `message = "model_error"` (fallback)
- control chars → replaced with space (`c.isprintable()` guard)
- truncated to 200 chars (`_BUS_MESSAGE_MAX_LEN`); suffix `…` if cut

Security boundary semantics UNCHANGED: `SanitizedError.message` still never carries `str(exc)` — `from_message` accepts only callee-controlled strings and applies guardrails on top.

## TurnPublisher (#1331)

`TurnPublisher` (transport-level) publishes `TurnWriteEvent` to JetStream
subject `lyra.turns.write`, awaiting PubAck before returning. Used by
pool/observer/inbound rewire (#1331) to replace direct TurnStore mutator
calls. Required `trace_id: str` per call (non-empty; threaded from
inbound msg.id when available, else uuid4().hex).

Consumer: `lyra.infrastructure.turn_writer.TurnWriter` (sole writer).
