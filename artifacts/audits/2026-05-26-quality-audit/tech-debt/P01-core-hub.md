### Summary

- **Zero action-item markers** (TODO/FIXME/HACK/XXX) and **zero deprecated API usage** in the 33-file partition. The dominant debt is architectural: 5 infrastructure store classes are imported directly into `core/hub`, bypassing `core/ports/` (ADR-048 migration gap).
- Three `DEBT:complexity-residual` markers remain in hot dispatch paths, and two stale `#753 Phase 3` comments persist after the epic closed, creating minor comment drift.
- One live transitional placeholder (`b""` in STT middleware) is correctly tracked by open issue #1067; no silent rot.

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `hub.py`, `hub_registration.py`, `hub_shutdown.py` | 35-39, 15-17, 13-14 | High | Direct imports from `lyra.infrastructure.stores.*` (IdentityAliasStore, MessageIndex, PairingManager, PrefsStore, TurnStore). No matching protocols exist in `core/ports/`, violating ADR-048. | Extract `Store` Protocols into `core/ports/` and switch imports to port types. |
| `outbound/_dispatch.py` | 106 | Low | Backoff delays are a literal tuple `(1.0, 2.0, 4.0)` without a named constant. | Extract `_BACKOFF_DELAYS` at module level. |
| `middleware/middleware_stt.py` | 110 | Low | Hardcoded `30000` ms fallback for STT timeout. | Extract `_DEFAULT_STT_TIMEOUT_MS = 30000`. |
| `pipeline/pool_manager.py` | 87 | Low | Magic divisor `10` used to throttle eviction scans (`_pool_ttl / 10`). | Extract `_EVICTION_THROTTLE_DIVISOR = 10`. |
| `hub_dispatch.py` | 41 | Medium | Comment references `#753 Phase 3`; issue #753 is closed. | Replace with concrete docstring or remove stale phase reference. |
| `outbound/outbound_router.py` | 3 | Medium | Docstring references `issue #753 Phase 3`; issue #753 is closed. | Replace with concrete docstring or remove stale phase reference. |
| `middleware/middleware_stt.py` | 116-117 | Medium | Transitional `b""` placeholder passed to `hub._stt.transcribe(...)` pending BlobRef resolution. | Consume `msg.audio.blob_ref` once #1067 / V8 (#1330) dependencies land. |
| `hub_protocol.py` | 14-26 | Medium | Self-documented ISP debt: `ChannelAdapter` Protocol fuses inbound + outbound roles. | Deferred per file comment; schedule a slice to split into `MessageReceiver` + `MessageSender` ports. |
| `middleware/middleware_stt.py`, `outbound/_dispatch.py`, `outbound/outbound_streaming.py` | 73, 27, 46 | Low | `DEBT:complexity-residual` noqa markers (C901 / PLR0915) on 3 high-traffic functions. | Schedule a small slice to extract helpers and drop noqa. |
| `middleware/middleware.py`, `middleware/middleware_submit.py`, `outbound/outbound_errors.py` | 64, 92, 122 | Low | `DEBT:boundary-broad-catch` (BLE001) on 3 top-level swallow-and-log boundaries. | Acceptable for boundary safety; no action unless telemetry demands richer classification. |
| `hub.py`, `pipeline/message_pipeline.py` | 19, 9 | Low | `DEBT:re-export-init` legacy re-exports to preserve external import paths. | Remove when downstream imports are confirmed migrated. |
| `tools/folder_exemptions.txt` | 9 | Low | `src/lyra/core` folder exemption aging: 14 files (limit 12), tracked by #858 / #1020. Not regressed (was 15). | Keep on backlog; remove exemption once folder drops to ≤12 files. |

### Metrics

| Metric | Value |
|--------|-------|
| Files analyzed | 33 |
| Total LOC | 3,909 |
| TODO / FIXME / HACK / XXX | 0 |
| Deprecated API usage | 0 |
| `DEBT:` markers | 13 |
| Unnamed magic numbers / tuples | 3 |
| ADR-048 direct infra imports (store classes) | 5 across 3 files |
| Stale closed-issue phase references | 2 (#753) |
| Open transitional placeholders | 1 (#1067) |
| Folder exemption overage (aging) | 2 files (`src/lyra/core`: 14 vs limit 12) |

### Recommendations (prioritized, max 5)

1. **Extract store ports for ADR-048 compliance** — Create `core/ports/` Protocols for `IdentityAliasStore`, `MessageIndex`, `PairingManager`, `PrefsStore`, and `TurnStore`, then switch `core/hub` imports to port types. This removes the largest architectural coupling in the partition.
2. **Clean up stale `#753 Phase 3` markers** — Replace or delete the two closed-epic references in `hub_dispatch.py` and `outbound/outbound_router.py` to prevent future readers from searching a finished roadmap.
3. **Name the three remaining magic numbers** — Extract `_DEFAULT_STT_TIMEOUT_MS`, `_BACKOFF_DELAYS`, and `_EVICTION_THROTTLE_DIVISOR` to improve readability and auditability.
4. **Resolve #1067 placeholder when dependencies land** — Wire `msg.audio.blob_ref` into `SttMiddleware` once V8 BlobStore Protocol (#1330) and #1065 are closed; the code location is already flagged.
5. **Slice complexity-residual reduction** — Schedule a small F-lite slice to break down `SttMiddleware.__call__`, `dispatch_outbound_item`, and `StreamingDispatch.dispatch` into helpers, removing the 3 `DEBT:complexity-residual` noqa markers.
