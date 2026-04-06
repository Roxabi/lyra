# Backend Review — NATS Wire Format — 2026-04-06

Commits reviewed:
- `520c33b` refactor(nats): unify InboundAudio into InboundMessage (Slice 1, #534)
- `d970d3a` feat(nats): add schema_version to wire format (#530)
- `747211e` fix(nats): rate-limit schema version mismatch logs + type annotations
- `aa57879` refactor(nats): extract outbound listener behind a protocol (#550)

---

## Overall Verdict

**WARN** — 2492 tests pass, 81.67% coverage, no regressions. Four findings worth
addressing before Slice 2 ships: one missing version check on the outbound path,
one runtime-only assert in SttMiddleware, one unchecked `_ENVELOPE_VERSIONS` KeyError
window, and one `ToolSummaryRenderEvent` decoded without a `schema_version` field
carried through to the reconstructed object.

---

## InboundMessage Unification

**AudioPayload field completeness** (`src/lyra/core/audio_payload.py:9`)

`AudioPayload` carries `audio_bytes`, `mime_type`, `duration_ms`, `file_id`, and
`waveform_b64`. All five fields from `InboundAudio` that are audio-specific are
present. The only `InboundAudio` fields not on `AudioPayload` are identity fields
(`id`, `platform`, `bot_id`, `scope_id`, `user_id`, `user_name`, `is_mention`,
`trust_level`, `trust`, `timestamp`, `platform_meta`, `routing`) — these are
correctly promoted to the wrapping `InboundMessage`. No audio data is lost.

**Compat shim conversion** (`src/lyra/nats/compat/inbound_audio_legacy.py:184–217`)

`_convert_legacy()` sets `waveform_b64=None` explicitly. `InboundAudio` never carried
a waveform — correct. All identity fields are propagated. `platform_meta` is passed
through `sanitize_platform_meta()` here (line 213), which is the correct #525-regression
guard that was missing in the original draft and fixed in the last sub-commit of
`520c33b`.

**Modality discriminator** (`src/lyra/core/message.py:103`)

`modality: Literal["text", "voice"] | None = None` — clean. `None` means "not set /
text assumed". The compat shim hardcodes `modality="voice"` (line 215). The STT
middleware correctly skips on `msg.modality != "voice"` (middleware_stt.py:83).

**Re-entrance guard** (`src/lyra/core/hub/middleware_stt.py:87`)

Guard is `if msg.text != ""` — passes through if text already populated. This is
correct for the compat shim path (text is empty on arrival) and for any future case
where text was set upstream.

**Dead code removal** — `AudioPipeline.run()` and `_process_audio_item()` deleted.
`_run_stt_stage` removed from `MessagePipeline`. The original `MessagePipeline` path
was confirmed dead (Hub uses `MiddlewarePipeline`). Deletion is clean.

**InboundAudio retention** — `InboundAudio` dataclass is kept in `message.py:112`.
This is correct: `OutboundListener.cache_inbound` still accepts `InboundMessage |
InboundAudio` (the protocol union covers Slice 2 deletion). The `test_outbound_listener_protocol.py:42`
comment documents this explicitly.

**No breaking changes to consumers** — `InboundMessage.audio` defaults to `None`
(message.py:109), `modality` defaults to `None` (line 103). Existing producers that
do not set these fields are unaffected (legacy wire-compatible).

---

## schema_version Implementation

**Coverage across envelope types** (`src/lyra/core/message.py:17–19`)

Three constants defined: `SCHEMA_VERSION_INBOUND_MESSAGE = 1`,
`SCHEMA_VERSION_INBOUND_AUDIO = 1`, `SCHEMA_VERSION_OUTBOUND_MESSAGE = 1`.
Two render-event constants in `src/lyra/core/render_events.py:22–23`.

**Check sites:**
- `NatsBus._make_handler` (nats_bus.py:257) — covers `InboundMessage` and `InboundAudio` inbound.
- `InboundAudioLegacyHandler._handle` (inbound_audio_legacy.py:136) — covers legacy audio.
- `NatsRenderEventCodec.decode` (render_event_codec.py:77, 89) — covers `TextRenderEvent` and `ToolSummaryRenderEvent`.

**Gap — OutboundMessage not version-checked on receive**
(`src/lyra/adapters/nats_outbound_listener.py:144`, 191)

`NatsOutboundListener._handle_send()` calls `_deserialize_dict(outbound_data, OutboundMessage)`
directly without a prior `check_schema_version` call. Same for `_handle_stream_start()`
at line 191. `SCHEMA_VERSION_OUTBOUND_MESSAGE` is defined but never consumed by any
receiver. The hub publishes `OutboundMessage` with `schema_version=1` (message.py:271
— dataclass default), so there is no immediate breakage, but a future bump to v2 would
silently misinterpret the payload on the adapter side. This is a forward-compat
blind spot.

**_ENVELOPE_VERSIONS KeyError window** (`src/lyra/nats/nats_bus.py:256`)

`envelope_name, expected = _ENVELOPE_VERSIONS[self._item_type]` runs inside the NATS
handler callback, not inside `start()`. An unregistered `item_type` raises `KeyError`
during live message dispatch, crashing the subscription callback silently (NATS swallows
handler exceptions). The PR review comment on `d970d3a` noted this should raise at
`start()` instead — the fix was merged for the `_fallback=1` removal, but the lookup
was not moved to `start()`. The crash is still deferred to handler time.

**Forward-compat rule** (`_version_check.py:87`)

`raw > expected → drop` — correct. Receiver accepts [1, expected], drops > expected.
Missing field → default 1 (legacy compat). Rule is sound.

**Bool exclusion** (`_version_check.py:77`)

`not isinstance(raw, int) or isinstance(raw, bool)` — correctly excludes JSON
`true`/`false` since `bool` is a subclass of `int` in Python.

**ToolSummaryRenderEvent decoded without schema_version field** (`render_event_codec.py:98–109`)

The `tool_summary` decode path passes the version check at line 89, then manually
reconstructs `ToolSummaryRenderEvent` from `payload.get(...)` calls. It does NOT
pass `schema_version` from the payload to the constructor — so the reconstructed
object always gets `schema_version=1` (the dataclass default). For v1 this is
harmless (correct), but when this envelope bumps to v2 and a new field is added,
the reconstructed object will silently carry `schema_version=1` while containing v2
data. Low severity now, but inconsistent with how `TextRenderEvent` is handled
(which uses `deserialize()` and preserves the field).

---

## Outbound Listener Protocol

**Protocol definition** (`src/lyra/adapters/outbound_listener.py:16`)

`OutboundListener` is a `typing.Protocol` with exactly three methods: `cache_inbound`,
`start`, `stop`. Not `@runtime_checkable` — consistent with `ChannelAdapter`
convention (noted in docstring at line 20). Clean.

**TYPE_CHECKING guard** (`outbound_listener.py:12–13`)

`InboundMessage` and `InboundAudio` imported under `TYPE_CHECKING` only. The
`cache_inbound` annotation `InboundMessage | InboundAudio` is correct — the union
will be resolved lazily under `from __future__ import annotations`. No circular
import risk.

**Adapter rewiring** — `TelegramAdapter._outbound_listener` typed as
`"OutboundListener | None"` (telegram.py:157). `DiscordAdapter._outbound_listener`
typed as `"OutboundListener | None"` (discord.py:124). Both import from
`lyra.adapters.outbound_listener` under `TYPE_CHECKING`. Concrete assignment at
runtime is still `NatsOutboundListener` (via `adapter_standalone.py` — unchanged).
The decoupling is complete at the type level.

**Conformance test** (`tests/adapters/test_outbound_listener_protocol.py`)

Three levels: module-level static tuple (forces mypy/pyright to verify assignability
at import time), annotated instance assignment (second static signal), runtime
method-surface check via `vars()` (immune to Protocol metaclass internals). Exercises
`cache_inbound` with a real `InboundAudio` to catch positional-arg drift. Solid.

**Protocol surface is exactly three methods** (test line 95) — the test asserts this
and would fail if `version_mismatch_count` were inadvertently added to the protocol.
Good guard.

---

## Type Safety

**`bootstrap_wiring.py` nats_client typed `Any`** (bootstrap_wiring.py — changed in
`520c33b`)

The comment in the PR notes this is intentional: `nats_client` was already a loose
parameter; widening to `Any` is a minimal fix for pyright. Acceptable short-term.
Tracked separately.

**`SttMiddleware` uses `assert` for Hub attribute access** (`middleware_stt.py:91`, 105)

`assert hub._msg_manager is not None` and `assert msg.audio is not None` are
runtime guards — correct for a production pipeline. However `assert` statements are
stripped when Python runs with `-O` (optimise flag). If the production supervisor
ever adds `-O`, both guards silently disappear. Prefer explicit `if ... raise
RuntimeError(...)` for safety-critical invariants in middleware.

**`_dispatch_error` uses `# type: ignore[union-attr]`** (`middleware_stt.py:190–191`)

Two ignores on `hub._msg_manager` and `hub.dispatch_response`. These exist because
`_dispatch_error` takes `hub: object` to avoid a circular import with `Hub`. Acceptable
as a workaround, but the pattern leaks private Hub internals through a type ignore.
A narrow protocol (`SttHubContext`) would be cleaner — low priority.

**`cast(InboundMessage, original_msg)` in NatsOutboundListener** (lines 148, 170, 261)

`_cache` is typed `dict[str, InboundMessage | InboundAudio]`. Casting to
`InboundMessage` before passing to `adapter.send()` and `adapter.render_attachment()`
is unsafe if the cache entry is actually `InboundAudio`. This predates the reviewed
commits and is a Slice 2 target (once `InboundAudio` is removed from the cache union),
but worth noting: if a legacy audio message arrives, is cached as `InboundAudio`, and
the hub then sends a non-streaming reply, the cast silently passes an `InboundAudio`
as `InboundMessage` to the adapter's `send()`.

**`decode_stream_events` return annotation** (`nats_stream_decoder.py:33`)

Correctly annotated `AsyncGenerator["RenderEvent", None]` following the fix in
`747211e`. Matches strict-mode pyright/mypy expectations.

---

## Test Coverage

| Test file | What it covers |
|-----------|---------------|
| `tests/core/hub/test_message_pipeline_stt.py` | 11 SttMiddleware cases (6 outcomes + re-entrance + modality=None pass-through + timeout + STTUnavailableError + slash-guard) |
| `tests/nats/compat/test_inbound_audio_legacy.py` | 5 compat shim cases (convert, schema mismatch, JSON error, deserialize error, bytes normalization) |
| `tests/core/test_bus_inject.py` | 3 inject cases (LocalBus, NatsBus, ordering) |
| `tests/nats/test_version_check.py` | 26 version check cases including rate-limit isolation, boolean drop, per-envelope log independence |
| `tests/adapters/test_outbound_listener_protocol.py` | 3 protocol conformance cases |
| `tests/integration/test_voice_end_to_end.py` | e2e voice path from compat shim through SttMiddleware |
| `tests/nats/test_nats_bus.py` | NatsBus version mismatch counting (extended: two v2_bad messages assert count==2) |

**Coverage gaps:**
- `nats_bus.py` at 53% — the `_make_handler` closure (lines 244–291) is not hit by
  unit tests because live NATS subscription callbacks require an actual NATS connection.
  The version-check path inside the handler is tested indirectly via the mocked
  handler in `test_nats_bus.py`, but coverage tooling does not attribute it to the
  source lines.
- `nats/compat/inbound_audio_legacy.py` at 75% — `start()` live-subscribe path
  (lines 93–104) not covered; correct (requires live NATS). Test-mode path is tested.
- `OutboundMessage` version-check gap has zero test coverage because no check exists
  (see Bugs section below).

---

## Bugs / Issues Found

Ranked by severity:

### 1. OutboundMessage deserialization skips schema_version check [WARN]

**File:** `src/lyra/adapters/nats_outbound_listener.py:144, 191`

`_handle_send()` and `_handle_stream_start()` call `_deserialize_dict(outbound_data,
OutboundMessage)` without calling `check_schema_version` first.
`SCHEMA_VERSION_OUTBOUND_MESSAGE = 1` is defined (`message.py:19`) but has no
consumer. A future OutboundMessage v2 will be silently accepted and misinterpreted
by v1 adapters. Not a current breakage (everything is v1), but the gap breaks the
"fail loudly" guarantee that motivated the entire `d970d3a` commit.

### 2. _ENVELOPE_VERSIONS KeyError deferred to handler time [WARN]

**File:** `src/lyra/nats/nats_bus.py:256`

`_ENVELOPE_VERSIONS[self._item_type]` is called inside the NATS message handler,
not during `start()`. An unsupported `item_type` raises `KeyError` inside the NATS
callback. NATS swallows handler exceptions (no re-raise), so the error is logged but
the bus silently stops processing. Moving this lookup to `start()` would surface the
misconfiguration before any messages arrive.

### 3. `assert` guards in SttMiddleware stripped by `-O` [LOW]

**File:** `src/lyra/core/hub/middleware_stt.py:91, 105`

`assert hub._msg_manager is not None` and `assert msg.audio is not None` are
safety-critical invariants. Python's `-O` flag strips all `assert` statements.
Replace with `if ... raise RuntimeError(...)` for production-grade middleware.

### 4. ToolSummaryRenderEvent decoded without schema_version [LOW]

**File:** `src/lyra/nats/render_event_codec.py:98–109`

The manual reconstruction at lines 98–109 does not forward `schema_version` from
`payload` to the `ToolSummaryRenderEvent` constructor. The object always gets
`schema_version=1` regardless of what was on the wire. Contrast with `TextRenderEvent`
which uses `deserialize()` and preserves the field. When `ToolSummaryRenderEvent`
bumps to v2, this will mask the version in reconstructed objects.

### 5. `cast(InboundMessage, ...)` is unsound for InboundAudio cache entries [LOW]

**File:** `src/lyra/adapters/nats_outbound_listener.py:148, 170, 261`

Cache entries may be `InboundAudio` (inserted via `cache_inbound`). All three
`cast(InboundMessage, original_msg)` calls are unsound when the entry is
`InboundAudio`. Slice 2 (#534) removes `InboundAudio` from the cache, but until
then this is a latent type error that could surface as an `AttributeError` if an
audio-originated reply passes through `_handle_send`.

---

## Recommended Actions

| Priority | Action | File:line |
|----------|--------|-----------|
| WARN | Add `check_schema_version` for `OutboundMessage` in `_handle_send` and `_handle_stream_start`; consume `SCHEMA_VERSION_OUTBOUND_MESSAGE` | `nats_outbound_listener.py:130–147`, `175–194` |
| WARN | Move `_ENVELOPE_VERSIONS[self._item_type]` lookup into `NatsBus.start()` to fail fast on misconfigured `item_type` | `nats_bus.py:138–154` |
| LOW | Replace `assert hub._msg_manager is not None` and `assert msg.audio is not None` with explicit `RuntimeError` raises | `middleware_stt.py:91, 105` |
| LOW | Forward `schema_version` from payload in `ToolSummaryRenderEvent` decode path, or switch to `deserialize()` like `TextRenderEvent` | `render_event_codec.py:98–109` |
| LOW | Track `cast(InboundMessage, ...)` calls as Slice 2 cleanup blockers; add a comment or `# type: ignore` with issue reference | `nats_outbound_listener.py:148, 170, 261` |
