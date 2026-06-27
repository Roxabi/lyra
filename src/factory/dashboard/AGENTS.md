# factory.dashboard — control-plane BFF axis (ADR-094)

## Two axes, one process

| Axis | Module | Routes |
|------|--------|--------|
| Chat transport | `factory.adapters.web` | `/api/chat`, `/api/stream`, `/api/agents` |
| BFF | `factory.dashboard` | `/api/bff/*`, static SPA |

Chat ingress uses `run_inbound_guarded` only — never duplicate the inbound pipeline here.

## Session ID taxonomy

| ID | Meaning | SSoT |
|----|---------|------|
| `WebMeta.session_id` | Browser/SSE transport correlation | Client + `WebSessionHub` |
| `stream_token` | SSE subscribe secret (interim #1992) | Minted on POST `/api/chat` |
| `cli_session_id` | Provider resume target | `turns.db` via hub TurnStore |
| Hub `session_id` | Conversation job identity | TurnStore `pool_sessions` |

**Never** treat browser `session_id` as `cli_session_id` for Reprendre.

## Import boundaries

- MUST NOT import `factory.infrastructure.stores.*`
- MUST NOT import `factory.inbound.*`
- Hub reads (sessions, status) via NATS RPC only