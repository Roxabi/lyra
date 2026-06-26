### Summary

- **Zero TODO/FIXME/HACK/XXX markers** across 43 files in `infrastructure/`, `transport/`, and `nats/` — surface is clean.
- **ADR-048 migration is stalled**: 6 concrete store classes are imported directly by `core/` code; 4 of them lack protocols entirely. This is the largest debt cluster in the partition.
- **3 deferred work tags and 2 transitional epic references** (#1061 V3/V4, #1067, #1221, C5, #1281) have no concrete resolution milestones — debt drain pressure is accumulating.

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `core/auth/authenticator.py` | 17 | High | Direct import of `AuthStore` concrete class from `lyra.infrastructure.stores.auth_store` — no `AuthStoreProtocol` exists in `core/stores/` | Extract `AuthStoreProtocol` to `core/stores/`; type-hint `authenticator` against it |
| `core/hub/hub.py` | 35-39 | High | Direct imports of `IdentityAliasStore`, `MessageIndex`, `PairingManager`, `PrefsStore`, `TurnStore` concrete classes from infrastructure | Extract missing protocols (`MessageIndexProtocol`, `PrefsStoreProtocol`, `TurnStoreProtocol`); use existing `IdentityAliasStoreProtocol` and `PairingManagerProtocol` |
| `core/cli/cli_pool.py` | 17 | High | Direct import of `TurnStore` concrete class; stores instance in `self._turn_store: TurnStore \| None` | Replace with `TurnStoreProtocol` once extracted |
| `core/pool/pool.py` | 13 | Medium | Direct import of `TurnStore` concrete class | Replace with `TurnStoreProtocol` |
| `core/hub/hub_registration.py` | 15-17 | Medium | Direct imports of `IdentityAliasStore`, `MessageIndex`, `TurnStore` concrete classes | Use `IdentityAliasStoreProtocol` (exists); extract `MessageIndexProtocol`, `TurnStoreProtocol` |
| `core/memory/memory.py` | 19 | Medium | Direct import of `IdentityAliasStore` concrete class | Use existing `IdentityAliasStoreProtocol` |
| `core/hub/hub_shutdown.py` | 13-14 | Medium | Direct imports of `MessageIndex`, `TurnStore` concrete classes | Extract protocols |
| `core/pool/pool_observer.py` | 8-9 | Medium | Direct imports of `MessageIndex`, `TurnStore` concrete classes | Extract protocols |
| `core/agent/agent.py` | 13 | Low | Direct import of `AgentStore` concrete class | Already has `AgentStoreProtocol` — migrate type hint |
| `core/agent/agent_refiner.py` | 16 | Low | Direct import of `AgentStore` concrete class | Already has `AgentStoreProtocol` — migrate type hint |
| `infrastructure/stores/sqlite_base.py` | 89 | Low | `PRAGMA busy_timeout=30000` — 30s timeout is unnamed constant | Introduce `_BUSY_TIMEOUT_MS = 30000` |
| `infrastructure/turn_writer/writer.py` | 79-80 | Medium | `ack_wait=60.0` and `max_deliver=5` duplicated inline from `stream_setup.py` constants `ACK_WAIT_SECONDS` / `MAX_DELIVER` | Import and reuse `ACK_WAIT_SECONDS` and `MAX_DELIVER` from `stream_setup` |
| `infrastructure/turn_writer/stream_setup.py` | 55 | Low | `duplicate_window=60` — unnamed constant in `_stream_config()` | Introduce `DUPLICATE_WINDOW_SECONDS = 60` |
| `nats/nats_tts_codec.py` | 101-105 | Medium | Transitional debt: `PENDING_STORE_KEY` blob resolution deferred to "epic #1061 V3/V4" / "#1067"; codec returns empty `audio_bytes` | File dedicated issue with concrete milestone for blob_ref → bytes resolution downstream |
| `nats/tts_engine_selector.py` | 5 | Low | Comment references dead code `TTSConfig/load_tts_config not included (issue #1221)` — stale marker | Verify #1221 status; remove comment if resolved, or reopen/track |
| `nats/nats_channel_proxy.py` | 93, 320, 333 | Low | `render_audio_stream()` / `render_voice_stream()` marked "not yet implemented (C5)" — no tracking issue | File issue or assign to existing epic with milestone for NATS audio/voice streaming |
| `transport/http_transport.py` | 1-45 | Low | `HttpTransport` skeleton ships `NOT_WIRED` errors (#1281) — present in main but never functional | Move to feature branch or implement; dead surface adds cognitive overhead |
| `tools/file_exemptions.txt` | — | Medium | `nats/render_event_codec.py` (388 lines) exemption cites #1192/#1205/#687; S4 resolution "DEFERRED — separate PR required" with no follow-up PR | Either split file now or assign to active issue with target date |
| `tools/file_exemptions.txt` | — | Medium | `nats/nats_channel_proxy.py` (361 lines) exemption cites #687/#1279 | Same as above |
| `tools/folder_exemptions.txt` | — | Medium | `infrastructure/stores/` (15 files) exemption cites #935 "ADR-048 store migration (4 files moved)" — migration incomplete, core still imports concrete classes | File follow-up issue to complete ADR-048: extract 4 missing protocols and eliminate all direct core→infrastructure store imports |

### Metrics

| Metric | Count |
|---|---|
| Files scanned | 43 |
| TODO / FIXME / HACK / XXX | 0 |
| Deprecated stdlib APIs (`asyncio.coroutine`, `utcnow`, `get_event_loop`) | 0 |
| `DEBT:` named tags in partition | 5 |
| Transitional epic references without concrete milestone | 4 |
| Concrete store classes imported by `core/` (ADR-048 violation) | 6 |
| Missing protocols in `core/stores/` for infrastructure stores | 4 |
| Existing protocols ignored by `core/` code | 2 |
| File exemptions aging (>3 months, no resolution PR) | 2 |
| Folder exemptions aging (>3 months, no resolution) | 1 |
| Unnamed magic constants | 3 |
| Skeleton / placeholder code (`HttpTransport`, audio/voice stream stubs) | 3 |

### Recommendations (prioritized)

1. **Complete ADR-048 protocol migration** — Extract `AuthStoreProtocol`, `MessageIndexProtocol`, `PrefsStoreProtocol`, and `TurnStoreProtocol` to `core/stores/`. Then swap all direct `core/` imports of concrete store classes to their protocols. This removes the largest architectural coupling in the partition. Target: single focused PR per protocol to keep reviews small.

2. **Close the blob_ref → bytes loop** — The `nats_tts_codec.py` and `nats_stt_codec.py` transitional window ("epic #1061 V3/V4", "#1067") has no concrete milestone. File a dedicated issue with acceptance criteria: codec must resolve `BlobRef.store_key` to audio bytes instead of surfacing empty bytes. This blocks TTS/STT pipeline completeness.

3. **Resolve or retire aging file exemptions** — `nats/render_event_codec.py` and `nats/nats_channel_proxy.py` are both >300 lines with exemptions that say "S4 DEFERRED — separate PR required". Assign each to an active issue with a target milestone, or execute the split now (both files have clear seam lines: codec entries vs. keepalive logic).

4. **Retire `HttpTransport` skeleton** — `transport/http_transport.py` has been a `NOT_WIRED` placeholder since #1281. Either implement it or move it out of the active tree. Its presence in `transport/` alongside functional `NatsTransport` creates false expectations for new contributors.

5. **Deduplicate `turn_writer` config** — `turn_writer/writer.py` inlines `ack_wait=60.0` and `max_deliver=5` while `stream_setup.py` already defines `ACK_WAIT_SECONDS` and `MAX_DELIVER`. Import and reuse the canonical constants to prevent config drift if JetStream tuning changes.
