# ADR Consolidation Matrix (2026-05-09)

Drives the consolidation of 46 ADRs into 9 domain pages + tombstoned archive.

## Domain mapping

| ADR | Title (≤60) | Domain | Status | Supersedes | Superseded_by | Summary |
|-----|-------------|--------|--------|------------|---------------|---------|
| 001 | RoutingKey platform/bot_id/scope_id | MSG | Amended | — | — | RoutingKey(platform,bot_id,scope_id) canonical; scope_id replaced user_id via #125 |
| 002 | Hub dispatch invariants & middleware | MSG | Amended | — | — | Adapter lookup, pool atomicity, RoutingKey.to_pool_id(); Hub middleware decomposed 2026-05-08 |
| 003 | Telegram webhook dispatch strategy | ADP | Accepted | — | — | feed_update() direct call (Option A); no queue |
| 004 | CliPool cwd resolution & annotation | WRK | Accepted | — | — | pyproject.toml anchor for root; AgentBase.__class__ annotation fixed |
| 005 | Wildcard binding & per-scope pools | WRK | Amended | — | — | Wildcard binding Phase 1; amended by #125 (per-scope pool IDs) |
| 006 | Hub run-loop error response gap | WRK | Accepted | — | — | User-facing error reply on agent failure (pool_processor_exec.py) |
| 007 | Model-config mismatch silent ignore | WRK | Accepted | — | — | Streaming respawns; non-streaming still silently ignores |
| 008 | Phase-1 memory scope levels | STO | Accepted | — | — | Levels 0 (working) + 3 (semantic) only; 1+2 deferred |
| 009 | Generic error reply placement | ENG | Accepted | — | — | GENERIC_ERROR_REPLY in core/message.py; breaks agents→hub coupling |
| 010 | External tool integration pattern | WRK | Accepted | — | — | Install+Wrap+Declare 3-layer pattern |
| 013 | Media temp-file lifecycle ownership | ADP | Accepted | — | — | Agent process() owns temp file cleanup |
| 014 | Adapter protocol gaps & inbound audio | ADP | Accepted | — | — | InboundAudio dead-end documented; AudioPayload via 039 |
| 015 | Outbound audio dispatch & reply-id | ADP | Accepted | — | — | All 4 audio/reply-id findings resolved 2026-05-08 |
| 019 | Multi-bot startup resource sharing | WRK | Accepted | — | — | Per-agent ProviderRegistry+MessageManager; shared CliPool |
| 020 | CLI entry-point dispatch strategy | ADP | Accepted | — | — | Dedicated lyra-agent entry in pyproject.toml |
| 022 | EventBus singleton → DI | STO | Amended | — | — | Migrated to DI; PipelineEventBus injected via Hub.__init__ |
| 023 | Per-user TTS prefs & overlay | ADP | Accepted | — | — | Per-call override (Option B) |
| 024 | AgentStore SQLite design | STO | Accepted | — | — | DB-first write ordering; AgentStore in lyra.infrastructure.stores |
| 026 | Pool callback wiring | WRK | Accepted | — | — | configure_pool() at pool resolution before _resolve_context |
| 028 | Token-level streaming path | LLM | Amended | — | 070 (partial) | Parallel streaming methods (Option A); SDK removed #666; edge-case 3 → 070 |
| 029 | DB-first agent config & hot-reload | STO | Accepted | — | — | DB updated_at polling; TOMLs become seed-only |
| 030 | Tool-provider session cmds | WRK | Accepted | — | — | ScrapeProvider/VaultProvider Protocols |
| 031 | ProcessorRegistry concurrent dispatch | WRK | Accepted | — | — | @register decorator; per-scope asyncio.Lock fan-out |
| 032 | LlmEvent→StreamProcessor→RenderEvent | LLM | Amended | — | — | Hexagonal streaming pipeline; SDK driver removed #666; extended by 070 |
| 035 | NATS subject naming | MSG | Accepted | — | — | domain-first lyra.{domain}.{qualifier}; all subjects conformant |
| 036 | RenderEvent streaming chunk protocol | MSG | Accepted | — | — | NatsChunkEnvelope with stream_id/seq/event_type/done |
| 038 | Health monitoring layer boundaries | WRK | Accepted | — | — | 3-layer observation; dead_backend_hits removed |
| 039 | STT/TTS NATS adapter decoupling | ADP | Accepted | — | — | lyra_stt+lyra_tts independent; hub never imports voicecli |
| 042 | Brand assets in roxabi-forge | OUT | Accepted | 034 | — | Brand assets → ~/.roxabi/forge/{project}/brand/; out of lyra |
| 045 | roxabi-nats SDK uv workspace | CTR | Accepted | 037,040,047,062 | — | packages/roxabi-nats/ uv workspace; absorbs 4 ADRs |
| 046 | Nkey provisioning declarative authconf | SEC | Accepted | — | — | auth.conf = pure function of IDENTITIES + seed dir |
| 049 | roxabi-contracts shared schema | CTR | Accepted | 044,050,066 | — | packages/roxabi-contracts/ Pydantic models; absorbs 3 ADRs |
| 051 | Per-identity NATS inbox prefix | SEC | Accepted | — | — | _INBOX.<identity>.> scoped prefix; closes wiretap risk |
| 052 | Voice registry authoritative routing | CTR | Accepted | — | — | WorkerRegistry single routing truth; queue-group fallback removed |
| 055 | Quadlet ecosystem conventions | DEP | Accepted | 053,054,056,068 | — | 7 cross-project Quadlet questions; absorbs 4 ADRs |
| 057 | Security event audit infra | SEC | Accepted | — | — | AuditSink Protocol + JetStreamAuditSink → LYRA_AUDIT |
| 058 | Typed error boundary | ENG | Accepted | — | — | LyraUserError hierarchy + ErrorBoundaryMiddleware + NullMessageManager |
| 059 | Hexagonal clean arch canonical | ENG | Accepted | 048,060 | — | 4-layer Domain→Application→Infrastructure→Adapters; absorbs 2 ADRs |
| 061 | Importlinter port import fix | WRK | Accepted | — | — | core imports from lyra.core.ports.*; 4 ignore_imports pending reduction |
| 063 | ThreadStore teardown ownership | STO | Accepted | — | — | close() removed from Protocol; bootstrap owns lifecycle |
| 064 | NATS ACL req/reply derivation | SEC | Accepted | — | — | Declarative request_reply_flows in acl-matrix.json; supersedes 062 Fix 2 |
| 065 | NATS KV readiness probe | MSG | Accepted | — | — | JetStream KV hub.ready in lyra-state replaces req/reply probe |
| 067 | BlobStore flat-FS content-addressed | STO | Accepted | — | — | BlobStore Protocol + SHA-256 flat-FS + SQLite index |
| 069 | Provision warn subid overlap | SEC | Accepted | — | — | Separate missing vs unreadable; whitelist for subid overlap |
| 070 | RenderEvent v2 AG-UI modeling | LLM | Accepted | — | — | 4 AG-UI event families; extends 032, partially supersedes 028 |
| 071 | CliPool Claude OAuth token | WRK | Accepted | — | — | type=env Podman secret for CLAUDE_CODE_OAUTH_TOKEN |

## Cross-listings (primary domain in table; cross-link in secondary doc)

- 036 (MSG primary, LLM secondary) — wire format encodes streaming protocol
- 057 (SEC primary, WRK secondary) — security infra with hex port/adapter split
- 032 (LLM primary, MSG secondary) — streaming pipeline runs on NATS
- 064 (SEC primary, MSG secondary) — ACL policy derived from req/reply subjects
- 061 (WRK primary, ENG secondary) — tooling enforcement of hex layer invariants

## Anomalies (to fix in passing)

1. ADR-035, 036, 058 self-describe as Draft/Proposed but are implemented → bump to Accepted
2. ADR-061 partially implemented (4 ignore_imports vs 2 target) — mark as such, link issue if exists
3. ADR-022 references nonexistent ADR-025 → strip reference or replace with PR/issue
4. ADR-007 silent-ignore on non-streaming path — open issue or note as known limitation
