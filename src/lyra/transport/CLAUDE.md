# src/lyra/transport/ — NATS Transport + Worker Pool

## Purpose

Domain-agnostic NATS transport primitives consumed by all domain worker clients
(`lyra.nats.*_client`, `lyra.llm.llm_client`). Lives here, NOT in `packages/roxabi-nats/`.

## Layer contract

```
NatsTransport          — call() + open_inbox() CM; owns NATS inbox lifecycle
WorkerPoolClient       — routing + CB + WorkerRegistry + heartbeat subscription
DomainClient           — thin wrapper in lyra.nats / lyra.llm (compose pool + codec)
```

## Key invariants

- `NatsTransport.open_inbox()` is an async context manager; the `InboxStream` it yields
  is only valid inside the `async with` block — never escape the CM.
- `WorkerPoolClient` owns the `WorkerRegistry` and heartbeat subscription. Domain clients
  MUST NOT subscribe to heartbeat subjects or open inboxes directly.
- `Result[T]` / `Ok[T]` / `Err` are the return types for all transport-level calls.
  `SanitizedError` strips internal detail before propagation to users (#1212).
- CB lives in `WorkerPoolClient` — domain clients must NOT add a second CB layer.
