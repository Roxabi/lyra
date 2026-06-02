## Code Smell Audit — P07 (infrastructure, transport, nats)

**Date:** 2026-05-26
**Scope:** `src/lyra/infrastructure/**/*.py`, `src/lyra/transport/**/*.py`, `src/lyra/nats/**/*.py`
**Context:** Epic #1277 stage-axis refactor active; prior audit 2026-05-18 covered hexagonal conformance / duplication / dead-code — not re-reported unless regressed.

---

### Summary

- **No functions exceed 100 lines** in non-exempt files; the sole >100-line function is `NatsRenderEventCodec.__init__` in an exempt file (388 lines, #1192).
- **8 DRY violation categories** identified, the most severe being triplicate NATS client boilerplate (`start`/`stop`/`is_available`), triplicate codec decode control-flow, and 5× repeated `TurnWriteEvent` envelope construction.
- **2 functions show elevated cognitive complexity** (>15): `PairingManager.validate_code()` and `NatsChannelProxy.send_streaming()`.
- **`identity_alias_store.py` sits at 299 lines** — one line below the file-length gate with no exemption tracking issue.

---

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `src/lyra/nats/nats_image_client.py` | 47 | Medium | `start`/`stop`/`is_available` copy-pasted verbatim across `NatsImageClient`, `NatsTtsClient`, `NatsSttClient` (~12 lines × 3 files). | Extract `NatsDomainClientBase` mixin or dataclass-style wrapper holding `pool` + `nc`. |
| `src/lyra/nats/nats_tts_client.py` | 26 | Medium | Same as above — triplicate boilerplate. | Same as above. |
| `src/lyra/nats/nats_stt_client.py` | 26 | Medium | Same as above — triplicate boilerplate. | Same as above. |
| `src/lyra/nats/nats_image_codec.py` | 97 | Medium | Decode control flow (`Err` check → `model_validate_json` → `ok` check) repeated identically in `ImageCodec`, `TtsCodec`, `SttCodec`. | Introduce generic `decode_result(result, ok_cls, error_factory)` utility or `CodecBase` abstract class. |
| `src/lyra/nats/nats_tts_codec.py` | 74 | Medium | Same as above — triplicate 15-line decode pattern. | Same as above. |
| `src/lyra/nats/nats_stt_codec.py` | 70 | Medium | Same as above — triplicate 15-line decode pattern. | Same as above. |
| `src/lyra/transport/turn_publisher.py` | 67 | Medium | 5 publish methods construct identical `TurnWriteEvent` envelope (contract_version, trace_id, issued_at, pool_id, session_id, platform, user_id, timestamp) — only `payload` differs. ~48 lines of repetition. | Extract `_make_event(pool_id, session_id, ..., payload)` helper; each publish method becomes 3 lines. |
| `src/lyra/nats/nats_channel_proxy.py` | 140 | Low | `json.dumps(..., ensure_ascii=False).encode("utf-8")` repeated 9 times; `serialize(...).decode("utf-8")` repeated 7 times. | Add `_to_json_bytes(dict)` and `_serialize_to_dict(obj)` private helpers. |
| `src/lyra/infrastructure/stores/pairing.py` | 130 | High | `validate_code()` spans 93 lines with nested transaction, attempt counting, expiry parsing, and AuthStore grant logic. Cognitive complexity ~20. | Extract `_consume_code(db, code_hash)` → `(bool, session_expires_at)` and `_grant_trusted(identity_key, expires_at)` helpers. |
| `src/lyra/nats/nats_channel_proxy.py` | 158 | High | `send_streaming()` spans 90 lines with nested try/except/finally, keepalive task coordination, terminal sentinel, error publishing, and iterator draining. Cognitive complexity ~18. | Extract `_publish_chunk(subject, chunk)`, `_publish_terminal(subject)`, and `_drain_iterator(events)`. |
| `src/lyra/nats/nats_channel_proxy.py` | 315 | Low | `render_audio_stream()` and `render_voice_stream()` are identical 5-line drain-with-log bodies. | Extract `_drain_unimplemented(chunks, inbound, feature_name)` helper. |
| `src/lyra/infrastructure/stores/identity_alias_store.py` | 75 | Low | File is 299 lines — 1 line below the 300-line gate, no exemption entry in `tools/file_exemptions.txt`. | File is stable; if next addition pushes it over 300, either extract `ChallengeManager` mixin or add exemption with tracking issue. |
| `src/lyra/infrastructure/stores/pairing.py` | 49 | Low | `PairingManager` and `IdentityAliasStore` both manually manage `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK` with identical try/except plumbing (~15 lines each). TurnWriter also repeats this pattern. | Introduce `stores.transaction.atomic(db)` async context manager in `sqlite_base.py`. |
| `src/lyra/infrastructure/stores/identity_alias_store.py` | 250 | Low | Same as above — manual transaction plumbing. | Same as above. |
| `src/lyra/infrastructure/turn_writer/writer.py` | 193 | Low | Same as above — manual BEGIN/COMMIT/ROLLBACK in `_handle_increment_resume_count`. | Same as above. |
| Multiple stores | — | Low | Inconsistent UTC-now generation: `identity_alias_store.py` and `auth_store.py` each define private `_utc_now()`; `agent_store.py` imports `_utc_now_iso`; `thread_store.py`, `turn_store.py`, `turn_store_session.py`, `message_index.py` inline `datetime.now(UTC).isoformat()`. | Move `_utc_now_iso()` to `sqlite_base.py` or a shared `stores._utils` module; unify all stores. |
| `src/lyra/nats/nats_image_codec.py` | 87 | Low | `model_dump_json(exclude_none=True).encode("utf-8")` repeated in all 3 codecs. | Extract `dump_contract(model) -> bytes` helper in `nats._utils` or use `roxabi_contracts` utility if available. |
| `src/lyra/nats/nats_tts_codec.py` | 71 | Low | Same as above. | Same as above. |
| `src/lyra/nats/nats_stt_codec.py` | 67 | Low | Same as above. | Same as above. |
| `src/lyra/nats/tts_engine_selector.py` | 40 | Low | `build_generate_kwargs()` (72 lines) merges 4 config layers manually with repetitive `if ... is not None` blocks. Comment says "merge-order inputs map 1:1 to config layers". | The explicit merge is readable and correct; complexity is linear, not nested. Leave as-is unless layer count grows past 6. |

---

### Metrics

| Metric | Count | % of 39 files |
|---|---|---|
| Total files analyzed | 39 | — |
| Files >100 lines | 10 | 26% |
| Files >100 lines (non-exempt) | 8 | 21% |
| Files >200 lines (non-exempt) | 8 | 21% |
| Functions >50 lines | 6 | — |
| Functions >100 lines (non-exempt) | 0 | — |
| Classes with >10 methods | 4 | — |
| DRY violation categories | 8 | — |
| High-severity findings | 2 | — |
| Medium-severity findings | 7 | — |
| Low-severity findings | 11 | — |

**File size distribution (non-exempt):**
- 100–200 lines: 4 files (turn_store_queries, worker_pool_client, turn_publisher, thread_store)
- 200–300 lines: 4 files (agent_store, pairing, identity_alias_store, nats_bus)
- 300+ lines: 2 exempt files (render_event_codec, nats_channel_proxy)

**Files at risk of crossing 300-line gate:**
- `identity_alias_store.py` (299 lines)

---

### Recommendations (prioritized)

1. **Extract NATS domain-client base class** (Medium)
   `NatsImageClient`, `NatsTtsClient`, `NatsSttClient` share identical `start`/`stop`/`is_available`. A `NatsDomainClientBase` holding `pool` + `nc` would cut ~36 lines of duplication and prevent future drift when heartbeat logic evolves.

2. **Unify codec decode pattern with a generic helper** (Medium)
   The 3-phase decode (`Err` → validation → `ok` check) is identical across `ImageCodec`, `TtsCodec`, `SttCodec`. A `decode_result(result, ResponseCls) -> TypedResult` utility removes ~30 lines of duplication and makes adding new domain clients (e.g. Video) trivial.

3. **Refactor `TurnPublisher` with envelope factory** (Medium)
   5 publish methods repeat the same `TurnWriteEvent` envelope. A single `_make_event(..., payload)` helper collapses the file from 203 → ~160 lines and eliminates the risk of envelope-field drift when `TurnWriteEvent` gains a new mandatory field.

4. **Add transaction context manager to `SqliteStore`** (Low)
   `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK` plumbing is copy-pasted in `pairing.py`, `identity_alias_store.py`, and `turn_writer/writer.py`. An `asynccontextmanager` on `SqliteStore` would make usage a 3-line `with` block and remove the risk of forgetting a ROLLBACK on exception.

5. **Standardize UTC-now helper across stores** (Low)
   Four different patterns for the same operation (`_utc_now()`, `_utc_now_iso`, `datetime.now(UTC).isoformat()`, `datetime.now(timezone.utc)`) live in 8 store files. Moving `_utc_now_iso()` to `sqlite_base.py` and converting all call sites reduces noise and prevents timezone-naive bugs.
