---
title: "Central Inbound Ingestion Stage — Design Analysis"
description: "Architecture design for absorbing adapter-side attachment ingest (#1065, #1066, #1067) into the stage axis per Epic #1277 §11."
issue: 1537
tier: F-lite
date: 2026-05-30
---

## 1. Current State

### Where ingest happens today (cited)

**Voice / audio path — Telegram**

`src/lyra/adapters/telegram/telegram_inbound.py:119-174` — `handle_voice_message()`:
1. Calls `_download_audio()` (telegram_audio.py) which uses `adapter.bot.get_file()` + `adapter.bot.download()` to write a tmp file.
2. Reads bytes from tmp file, then calls `normalize_audio()`.
3. `telegram_normalize.py:275-290` — `normalize_audio()` stamps `BlobRef(store_key=PENDING_STORE_KEY, content_hash="", ...)` — bytes are **dropped** after this call. The `audio_bytes` parameter is used only for `size=len(audio_bytes)`.

**Non-audio attachments — Telegram**

`src/lyra/adapters/telegram/telegram_normalize.py:26-81` — `_extract_attachments()` builds `Attachment` objects with `url_or_path_or_bytes=f"tg:file_id:{file_id}"` strings. No download, no store. The platform reference is kept as an opaque string.

**Voice / audio path — Discord**

`src/lyra/adapters/discord/discord_audio.py:128-260` — `handle_audio()`:
1. Pre-download size check, then `await audio_attachment.read()` (signed CDN URL fetch).
2. Magic-byte validation (`is_valid_audio_magic`).
3. Calls `normalize_audio()` at line 240.
4. `discord_audio.py:61-125` — `normalize_audio()` stamps `BlobRef(store_key=PENDING_STORE_KEY, content_hash="", platform_ref=None, ...)`. Bytes are **dropped** — used only for `size=len(audio_bytes)`.

**Non-audio attachments — Discord**

`src/lyra/adapters/discord/discord_normalize.py:83` — `extract_attachments()` (delegated to `discord_formatting.py`). Discord CDN URLs are placed in `Attachment.url_or_path_or_bytes`. No download, no store.

**STT worker side (#1067)**

`src/lyra/nats/nats_stt_client.py:53-77` — `transcribe()` accepts `BlobRef | bytes`. When given a `BlobRef` with `store_key=PENDING_STORE_KEY`, the codec currently sends it as-is; the STT worker (voiceCLI) must fall back to `platform_ref` (Telegram `file_id`) or inline bytes to obtain the audio. The `PENDING_STORE_KEY` sentinel is defined in `packages/roxabi-contracts/src/roxabi_contracts/blob_ref.py:16` with an explicit removal note.

### Why this is the N×M trap

The attachment-ingest concern is implemented per-platform (N=2, Telegram + Discord) × per-type (voice, image, document, video, animation, sticker). That is N × M implementation points with identical logic: download bytes → compute hash → store → emit `BlobRef`. Per ADR-073's pattern: each new platform must re-implement all M type handlers; each new type requires edits in all N platform files. The `ingest_bytes_to_blob_ref()` helper in `packages/roxabi-blobs/ingest.py` was written explicitly to close this trap (its module docstring says "prevent N×M adapter duplication (ADR-073 three-strikes)") but was never wired in — confirmed by `git log -S ingest_bytes_to_blob_ref -- src/lyra/adapters/` returning no hits.

---

## 2. Stage Placement

### Decision

Ingestion is a **new dedicated stage** inserted into the inbound pipeline between `parse` and `pre_route_hook`. Call it `AttachmentIngestStage`.

### Before / after pipeline diagram

**Before (current)**:

```
[Telegram adapter]                          [Discord adapter]
  handle_voice_message()                      handle_audio()
    download bytes                              .read() bytes
    normalize_audio()                           normalize_audio()
      → BlobRef(PENDING_STORE_KEY)               → BlobRef(PENDING_STORE_KEY)
    push_to_hub_guarded()                     push_to_hub_guarded()
                                                        ↓
                                             [Bus → Hub → SttMiddleware]
                                               transcribe(BlobRef)
                                               BlobRef.store_key == PENDING  → fallback to platform_ref
                                               STT failure → bytes already dropped → LOST
```

**After (proposed)**:

```
[Telegram adapter]                          [Discord adapter]
  handle_voice_message()                      handle_audio()
    _download_audio() → bytes                   .read() → bytes
    → InboundMessage(                           → InboundMessage(
        audio=AudioPayload(                         audio=AudioPayload(
          blob_ref=BlobRef(PENDING),                  blob_ref=BlobRef(PENDING),
          ...),                                        ...),
        attachments=[                               attachments=[
          PendingAttachment(fetch_closure)])          PendingAttachment(fetch_closure)])

InboundPipeline.run()
    parse()    ← unchanged
       ↓
    AttachmentIngestStage.run(msg, ctx)   ← NEW
       for each PendingAttachment:
         bytes = await fetch_closure()
         ref = await ingest_bytes_to_blob_ref(store, bytes, ...)
         → replaces PendingAttachment with real BlobRef in Attachment
       for audio (modality==voice):
         ref = await ingest_bytes_to_blob_ref(store, bytes, ...)
         → replaces blob_ref in AudioPayload (PENDING → real)
       ↓
    pre_route_hook(opt)
       ↓
    Router → [DROP|PROCESS]
       ↓
    pre_session_hook(opt) → SessionBuilder → Dispatcher
       ↓
                                             [Bus → Hub → SttMiddleware]
                                               transcribe(BlobRef)
                                               BlobRef.store_key is REAL → store.get()
                                               STT failure → blob already in store → preserved
```

### Rationale

**Not fold-into-parse:** `WireParser.parse()` is sync and pure (`wire_parser.py:21`). Ingest is async I/O — structural mismatch. The WireParser Protocol cannot be widened without breaking all implementations.

**Not a pre_route_hook:** Hooks are optional and platform-specific (bound via `functools.partial` with adapter reference). Ingest is a universal concern, not platform-specific. Using a hook would require both adapters to independently supply it — recreating the N×M pattern.

**Not post-dispatch:** Ingest must complete before the message reaches the bus and `SttMiddleware`, to guarantee blob durability before STT runs.

**A new stage (chosen):** `InboundPipeline.run()` inserts `AttachmentIngestStage` as a mandatory step after `parse` and before `pre_route_hook`. It receives an `IngestCtx` (contains the `BlobStore` reference) and the `InboundMessage`. The stage is stateless; the `BlobStore` reference lives in `InboundContext`. Stage modules in `src/lyra/inbound/` contain no platform-library imports — the fetch closure is the only platform bit crossing the boundary.

**Audio path inversion:** Currently `handle_voice_message` (Telegram) and `handle_audio` (Discord) download bytes, then create an `InboundMessage` with `PENDING_STORE_KEY`. They bypass the pipeline entirely, calling `push_to_hub_guarded()` directly. Post-design: adapters create a light `InboundMessage` with the audio bytes captured in a fetch closure, run it through the pipeline, and let `AttachmentIngestStage` perform the store write. The `handle_voice_message`/`handle_audio` functions are reduced to parsing + closure construction, then delegating to `InboundPipeline.run()` (same pattern as `handle_message`).

---

## 3. Attachment Descriptor + Fetch Closure Contract

### Platform-agnostic types (adapter emits, stage consumes)

```python
# src/lyra/inbound/attachment_ingest.py

from __future__ import annotations
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


# A closure that fetches raw bytes for one attachment unit.
# Supplied by the adapter; contains no platform types.
FetchFn = Callable[[], Awaitable[bytes]]


@dataclass(frozen=True)
class PendingAttachment:
    """Platform-agnostic descriptor for an attachment pending ingest.

    Emitted by wire parsers; consumed by AttachmentIngestStage.
    Contains no discord/aiogram types — only stdlib + callable.
    """
    fetch: FetchFn                # async closure: () -> bytes
    mime: str                     # MIME type (trusted from platform or sniffed)
    source: str                   # "telegram" | "discord" | ...
    platform_ref: str | None      # opaque: "tg:file_id:{id}" or Discord URL
    platform_message_id: str | None
    filename: str | None = None


@dataclass(frozen=True)
class IngestCtx:
    """Ingest-stage context — injected into InboundContext."""
    store: "BlobStorePort | None"  # lyra.core.ports.blobstore — Protocol, never concrete infrastructure type
    # Rationale: the stage imports the core/ports Protocol (BlobStorePort), never infrastructure/roxabi_blobs
    # concrete types — satisfies the import-layer contract. Ref: ADR-082.


class AttachmentIngestStage:
    """Resolves all PendingAttachment descriptors into real BlobRefs.

    For each PendingAttachment in msg.attachments:
      1. Awaits fetch() closure to get raw bytes.
      2. Calls ingest_bytes_to_blob_ref(store, bytes, ...).
      3. Replaces the Attachment.url_or_path_or_bytes with the BlobRef store_key
         (or stores the BlobRef inline if InboundMessage.Attachment gains a blob_ref field).

    For voice messages (modality=="voice"):
      4. Awaits AudioPayload's associated fetch closure.
      5. Replaces AudioPayload.blob_ref (PENDING → real BlobRef).

    On store=None or fetch failure: falls back gracefully (keeps PENDING / logs warning).
    """

    async def run(
        self,
        msg: "InboundMessage",
        ctx: "InboundContext",
    ) -> "InboundMessage":
        """Returns an InboundMessage with all BlobRefs resolved."""
        ...
```

### How it satisfies `inbound-no-adapters`

The `.importlinter` contract `inbound-no-adapters` forbids `lyra.inbound` → `lyra.adapters` imports. The fetch closure (`FetchFn = Callable[[], Awaitable[bytes]]`) is a plain Python callable — it captures adapter-specific state (aiogram Bot, aiohttp session, Discord attachment proxy) inside the adapter before being injected into the descriptor. The descriptor type `PendingAttachment` imports only stdlib and `roxabi_blobs`/`roxabi_contracts`. `AttachmentIngestStage` imports `ingest_bytes_to_blob_ref` from `roxabi_blobs` and `BlobStore` from `roxabi_blobs.protocol` — neither is in `lyra.adapters`.

**Pattern precedent:** This is identical to how `pre_route_hook` and `pre_session_hook` work: adapter-side closures bound via `functools.partial` are injected into the pipeline call, carrying platform-specific context without polluting generic stage modules. The `FetchFn` is a closure, not a method on an adapter-typed argument.

### Injection mechanism

The fetch closure is constructed in the adapter's `handle_voice_message` / `handle_audio` / `handle_message` (for attachment messages), captured with a lambda or partial, and placed in `PendingAttachment`. Example for Telegram voice:

```python
# adapter side (telegram_inbound.py) — sketch only
async def _make_voice_fetch(adapter, file_id: str) -> FetchFn:
    async def _fetch() -> bytes:
        tmp_path, _ = await _download_audio(adapter, file_id)
        try:
            return tmp_path.read_bytes()
        finally:
            tmp_path.unlink(missing_ok=True)
    return _fetch

# PendingAttachment(fetch=await _make_voice_fetch(adapter, file_id), mime="audio/ogg", ...)
```

The `IngestCtx` is added to `InboundContext` as a new field:

```python
@dataclass(frozen=True)
class InboundContext:
    router: RouterCtx
    session: SessionCtx
    dispatch: DispatchCtx
    ingest: IngestCtx           # NEW — None-safe: ingest skipped when store=None
    # IngestCtx.store is typed BlobStorePort | None (lyra.core.ports.blobstore), per ADR-082.
```

`InboundPipeline.run()` signature gains an `ingest_stage` optional parameter (defaulting to `None` for backward compat until the context field lands):

```python
async def run(
    self, raw, ctx, parser, *,
    pre_route_hook=None,
    pre_session_hook=None,
    send_backpressure,
    on_drop=None,
) -> None:
    msg = parser.parse(raw, ctx)
    if msg is None:
        return
    if self._ingest_stage is not None and ctx.ingest.store is not None:
        msg = await self._ingest_stage.run(msg, ctx)
    ...  # existing pipeline continues unchanged
```

---

## 4. Attachment-Type Coverage

| Type | Telegram source | Discord source | Mime (representative) | Current handling | Post-design handling |
|------|----------------|----------------|----------------------|-----------------|---------------------|
| Voice/audio | `voice`, `audio`, `video_note` — `file_id` via `bot.get_file()` + `bot.download()` | `attachment.content_type in AUDIO_MIME_TYPES`, signed CDN URL, `attachment.read()` | `audio/ogg`, `audio/mpeg` | Downloaded in `handle_voice_message`/`handle_audio`; PENDING_STORE_KEY; bytes dropped | `PendingAttachment` with fetch closure; `AttachmentIngestStage` stores + stamps real `BlobRef` in `AudioPayload` |
| Image/photo | `msg.photo[-1].file_id` → `tg:file_id:{id}` | `attachment.url` (CDN URL) | `image/jpeg`, `image/png` | `url_or_path_or_bytes=f"tg:file_id:{id}"` string; no download; no store | Fetch closure wraps `bot.get_file()` + download (Telegram) or `aiohttp.get(url)` (Discord); stage stores + `Attachment.blob_ref` set |
| Document | `msg.document.file_id` → `tg:file_id:{id}` | `attachment.url`, `attachment.content_type` | `application/pdf`, `application/octet-stream`, etc. | Same as image — opaque string ref only | Same as image |
| Video | `msg.video.file_id` → `tg:file_id:{id}` | `attachment.url` | `video/mp4` | Same — string ref | Same as image |
| Animation/GIF | `msg.animation.file_id` | `attachment.url` with `.gif`/`.mp4` | `image/gif` | `type="image"`, string ref | Same as image |
| Sticker (static) | `msg.sticker.file_id` (only non-animated, non-video) | Not detected today | `image/webp` | String ref; animated/video stickers skipped | Static stickers: fetch closure. Animated (`.tgs`) and video (`.webm`) continue to be skipped — out of scope for V1 |
| Sticker (animated/video) | Skipped (`is_animated=True` or `is_video=True`) | — | `application/x-tgsticker`, `video/webm` | Not handled | Not handled (unchanged) |

---

## 5. Contract Changes

### AudioPayload — no generalization needed

`AudioPayload` (`src/lyra/core/audio_payload.py`) remains voice-only. Non-audio attachment types are handled through `InboundMessage.attachments: list[Attachment]`. This avoids widening `AudioPayload`'s semantics.

**Required change to `Attachment`:** The current `Attachment.url_or_path_or_bytes: str | bytes` field is a platform-specific opaque value. Post-ingest it needs to carry a resolved `BlobRef`. Two options:

- Option A: Add `blob_ref: BlobRef | None = None` field to `Attachment`. After ingest, `blob_ref` is set and `url_or_path_or_bytes` retains the original reference for debugging / fallback.
- Option B: Encode the `store_key` in `url_or_path_or_bytes` with a sentinel prefix (`"blob:{store_key}"`).

Option A is cleaner (typed, explicit) and consistent with `AudioPayload.blob_ref`. Recommended.

### BlobRef flow

```
[Adapter] construct PendingAttachment(fetch_closure, mime, source, platform_ref)
    ↓
[AttachmentIngestStage]
    bytes = await fetch_closure()
    ref: roxabi_blobs.BlobRef = await ingest_bytes_to_blob_ref(store, bytes, ...)
    # Convert to wire-side BlobRef (roxabi_contracts) using the factory method:
    wire_ref: roxabi_contracts.BlobRef = roxabi_contracts.BlobRef.from_store_ref(ref)
    # Note: the manual 7-field copy above was the provenance-loss anti-pattern eliminated by ADR-082.
    # It silently dropped filename, platform_ref, platform_message_id, and created_at.
    # from_store_ref() is the single correct conversion point. Ref: ADR-082.
    # stamp into AudioPayload or Attachment.blob_ref
    ↓
[SttMiddleware] transcribe(msg.audio.blob_ref)
    → store_key is real → store.get(store_key) succeeds
```

Note: `roxabi_blobs.BlobRef` and `roxabi_contracts.BlobRef` are parallel types (identical field shapes, different packages). Both are already present in the codebase. The stage ingests via `roxabi_blobs` (store access) and stamps `roxabi_contracts.BlobRef` on `InboundMessage` (cross-process wire type). See `roxabi_blobs/CLAUDE.md` §"BlobRef ownership" — this dual-type pattern is the documented design.

Note: `IngestCtx.store: BlobStorePort | None` now consumes a real merged port — **#1540 (BlobStorePort) is DELIVERED** (PR #1546 merged 2026-05-30). The `BlobStorePort` interface in `lyra.core.ports.blobstore` is the type to import in the stage.

### Transport & payload (validated 2026-05-30)

- **Byte transport:** bytes flow via HTTP `BlobStorePort.put()` single-shot. NATS carries only the `BlobRef` (wire ref), never raw bytes. This is correct — the NATS message-size cap does not apply to blob content.
- **`put_multipart` scope:** `put_multipart()` (#1334) is an additive, non-breaking Protocol extension. V1 uses single-shot `put()` only. Deferring multipart causes zero wire/contract churn — the `put()` path is entirely unaffected. `put_multipart` remains out-of-scope for this stage.
- **Non-audio ceiling — GAP:** Non-audio attachments (images, documents, video, sticker) have **no size cap in code today** (`telegram_normalize.py:26-81` passes opaque `tg:file_id:{id}` strings; `discord_formatting.py:67-88` passes CDN URLs — zero download). The `AttachmentIngestStage` will be the **first site to download them**. It MUST impose an explicit ceiling before download:
  - Recommended: `MAX_ATTACHMENT_INGEST_BYTES = 20 MiB` (= Telegram `getFile` cap). Reject + log + user-facing error before any `fetch()` call if `PendingAttachment.size` (if known) exceeds this, or abort mid-stream and raise immediately on byte count exceeded.
  - Audio path: real cap is `LYRA_MAX_AUDIO_BYTES` (default **5 MiB** — `telegram.py:124-125`, `discord/adapter.py:113-114`), NOT the TG 50 MB / Discord 100 MB platform limits.
- **HTTP 500 on oversize:** The blobstore HTTP service returns **500 (not 413)** on oversize payloads (`await request.body()` is unbounded in current server). The stage MUST catch HTTP 500 from `put()` and surface a user-facing error — do not propagate 500 silently.
- **Write timeout:** `HttpBlobStore` write timeout is 5 s (`http_store.py:89`). If the stage streams CDN→PUT (Discord eager-ingest timing, CDN URLs expire within minutes), the 5 s idle window may be insufficient for large attachments over a slow link. Per-call timeout override should be assessed at implementation.
- **Discord CDN URL expiry:** Discord CDN URLs expire within minutes. The stage must ingest eagerly on message receipt (not lazily). Timing of S4/S7 wiring must confirm the fetch happens within the pipeline, before the message is enqueued on the bus.

### PENDING_STORE_KEY removal sequencing

1. `AttachmentIngestStage` lands and is wired in both adapters' audio paths → voice `BlobRef` is always real before the bus.
2. `SttMiddleware` (`middleware_stt.py:116`) calls `transcribe(msg.audio.blob_ref, ...)` — this already passes the `BlobRef` directly; no code change needed at the STT call site.
3. `NatsSttClient.transcribe()` removes the `isinstance(audio, bytes)` branch and the inline `PENDING_STORE_KEY` construction (`nats_stt_client.py:57-64`).
4. Non-audio `Attachment.blob_ref` is populated by the stage; consumers are updated to use it.
5. `PENDING_STORE_KEY` sentinel and the `_require_content_hash_unless_sentinel` validator in `roxabi_contracts.blob_ref` are removed after all three adapters are confirmed producing real `BlobRef`s (guarded by a CI check).
6. `BlobRef.platform_ref` field is retained as a permanent provenance field (original platform handle for audit), not a fallback mechanism.

---

## 6. Ingest-Before-STT Ordering

The ordering guarantee is structural, not documented:

```
InboundPipeline.run() execution sequence:
  1. parse()                   → InboundMessage with PendingAttachment + PENDING BlobRef
  2. AttachmentIngestStage.run() → awaited; returns InboundMessage with real BlobRef
  3. pre_route_hook (opt)
  4. Router.decide()
  5. pre_session_hook (opt)
  6. SessionBuilder.build()
  7. Dispatcher.dispatch()     → pushes to inbound bus
                                       ↓
                               Hub → SttMiddleware  ← receives real BlobRef
```

`AttachmentIngestStage.run()` is `await`-ed inline before `Dispatcher.dispatch()`. The dispatcher pushes to `inbound_bus` only after the stage completes. `SttMiddleware` runs inside the hub's middleware pipeline, which only fires after the message is dequeued from the bus. Since `AttachmentIngestStage` runs before the bus enqueue, blob durability is guaranteed before STT runs — no concurrency or ordering ambiguity.

### STT-failure `_DROP` path

Under the current design, STT failure (`_DROP` at `middleware_stt.py:97–153`) loses the audio bytes because they were already dropped after `normalize_audio()` stamped `PENDING_STORE_KEY`. After this design:

- The blob is already in `BlobStore` when `SttMiddleware` runs.
- STT failure drops the `InboundMessage` (the pipeline result is `_DROP`) but the blob persists in the store.
- Recovery paths (replay, manual retranscription) can call `store.get(blob_ref.store_key)` using the `BlobRef` stored in the dropped message's `audio.blob_ref`.
- No code change is required in `SttMiddleware` for this property — it falls out of the ordering guarantee.

### Fetch closure failure handling

If `fetch_closure()` raises (network error, size limit exceeded, magic-byte check fails), `AttachmentIngestStage` logs the failure and either:
- Returns the original `InboundMessage` with `PENDING_STORE_KEY` (degraded mode, preserved backward compat with STT's `platform_ref` fallback path), OR
- Returns `None` to signal the pipeline to drop the message (same as `WireParser.parse()` returning `None`).

The recommended behavior is **degraded mode** for voice (allows STT `platform_ref` fallback path until fully cut over) and **drop + error reply** for non-audio attachments (the attachment is the content; without it the message has no value). This is a decision for implementation — flagged under §8.

---

## 7. Slice Decomposition

Each slice is ≤F-lite. Ordered by dependency; can be executed sequentially.

| # | Slice | Files affected | Falsifiable AC | Maps to |
|---|-------|---------------|----------------|---------|
| S1 | `PendingAttachment` descriptor + `FetchFn` type + `IngestCtx` | `src/lyra/inbound/attachment_ingest.py` (new), `src/lyra/inbound/context.py` | `ingest-no-adapters` importlinter contract passes; `PendingAttachment` can be constructed with a plain `async def` closure; no `discord`/`aiogram` import anywhere in `lyra.inbound` | New infrastructure |
| S2 | `AttachmentIngestStage` — voice path only (audio/ogg, PENDING→real) | `src/lyra/inbound/attachment_ingest.py`, `src/lyra/inbound/pipeline.py` | Unit test: stage given `AudioPayload(PENDING_STORE_KEY)` + fetch closure → emits message with real `BlobRef`; `store_key != PENDING_STORE_KEY` | #1065 completion (Telegram voice ingest) |
| S3 | Wire up Telegram voice path: `handle_voice_message` → fetch closure → pipeline | `src/lyra/adapters/telegram/telegram_inbound.py`, `src/lyra/adapters/telegram/telegram_normalize.py` | E2E test: Telegram voice message → `ingest_bytes_to_blob_ref` called once → `InboundMessage.audio.blob_ref.store_key != PENDING_STORE_KEY` before hub enqueue | #1065 completion |
| S4 | Wire up Discord voice path: `handle_audio` → fetch closure → pipeline | `src/lyra/adapters/discord/discord_audio.py`, `src/lyra/adapters/discord/discord_inbound.py` | Same AC as S3 for Discord | #1066 supersede |
| S5 | `AttachmentIngestStage` — non-audio attachment path (image/document/video/sticker/animation) | `src/lyra/inbound/attachment_ingest.py`, `src/lyra/core/messaging/message.py` (`Attachment.blob_ref` field) | Unit test: stage given `Attachment(url_or_path_or_bytes="tg:file_id:x")` + fetch closure → `Attachment.blob_ref` is a real `BlobRef`; `store_key != PENDING_STORE_KEY` | #1065 (all types), #1066 (Discord non-audio) |
| S6 | Wire up Telegram non-audio attachments: `_extract_attachments` emits fetch closures | `src/lyra/adapters/telegram/telegram_normalize.py` | E2E test: Telegram image message → `Attachment.blob_ref` populated before hub | #1065 |
| S7 | Wire up Discord non-audio attachments | `src/lyra/adapters/discord/discord_normalize.py`, `src/lyra/adapters/discord/discord_formatting.py` | Same AC as S6 for Discord | #1066 |
| S8 | STT worker path cleanup: remove PENDING_STORE_KEY construction in `NatsSttClient.transcribe()` + verify `platform_ref` fallback can be removed | `src/lyra/nats/nats_stt_client.py` | CI: no remaining `PENDING_STORE_KEY` construction at adapter ingest sites; STT integration test passes with real BlobRef | #1067 coordination |
| S9 | PENDING_STORE_KEY retirement: remove sentinel, validator, `cli_voice_smoke.py` usage | `packages/roxabi-contracts/src/roxabi_contracts/blob_ref.py`, `src/lyra/cli_voice_smoke.py`, `src/lyra/nats/nats_tts_codec.py` | CI: `grep -r PENDING_STORE_KEY src/` returns 0 hits | #1065 + #1066 completion |

**Issue disposition:**
- **#1065** (Telegram adapter eager-ingest) — S2, S3, S5, S6 collectively deliver its original scope. Mark closed-complete after S6.
- **#1066** (Discord adapter eager-ingest) — S4, S7 deliver Discord symmetry. Mark closed-complete or superseded after S7.
- **#1067** (workers consume BlobRef, drop platform_ref fallback) — S8 delivers the coordination requirement. Mark closed-complete after S8.

---

## 8. Risks / Open Questions

### Decision A — Fetch failure behavior for non-audio attachments

```
── Decision: Attachment fetch failure disposition ──
Context:     fetch_closure() can fail (network error, size limit). For non-audio
             attachments the attachment IS the content. For voice the STT platform_ref
             fallback exists as a temporary escape hatch.
Target:      Clear, user-visible error behavior; no silent data loss.
Path:        Implement chosen option in AttachmentIngestStage.run() error branch.

Options:
  1. Drop + error reply — pipeline returns None; adapter sends "attachment unavailable" error reply
  2. Degraded mode — keep PENDING_STORE_KEY; let downstream consumer handle (STT platform_ref fallback, or agent sees empty attachment)   ← recommended for voice during transition
Recommended: Option 2 for voice (preserves STT fallback during transition), Option 1 for non-audio (attachment IS the content; degraded is meaningless)
```

### Decision B — `Attachment` type evolution

```
── Decision: Add blob_ref field to Attachment ──
Context:     Attachment.url_or_path_or_bytes carries opaque platform refs today.
             After ingest, a typed BlobRef is available. Two ways to carry it.
Target:      Typed access to BlobRef by downstream consumers (agents, hub outbound).
Path:        Modify InboundMessage.Attachment in lyra.core.messaging.message.

Options:
  1. Add blob_ref: BlobRef | None = None field alongside url_or_path_or_bytes  ← recommended
  2. Encode store_key in url_or_path_or_bytes with "blob:{key}" prefix sentinel
Recommended: Option 1 — typed, explicit, consistent with AudioPayload.blob_ref pattern. Requires checking all existing Attachment consumers for the new field.
```

### Decision C — Ingest context availability in test/CLI mode

```
── Decision: BlobStore availability when store=None ──
Context:     CLI mode and many tests have no BlobStore. IngestCtx.store=None
             must be safe.
Target:      Zero regressions in existing test suite.
Path:        AttachmentIngestStage short-circuits when ctx.ingest.store is None (passthrough mode).

Options:
  1. Stage is no-op when store=None (PENDING_STORE_KEY preserved)   ← recommended
  2. Stage raises if store=None (force explicit wiring)
Recommended: Option 1 — matches existing optional-field patterns (turn_publisher=None, thread_store=None) in context.py.
```

### Open question — Animated/video sticker handling

Telegram animated stickers (`.tgs`, Lottie JSON-based) and video stickers (`.webm`) are currently skipped in `_extract_attachments`. Should the stage handle them? Decision deferred to product — out of scope for S5/S6.

---

## 9. Non audité

The following items from the read-list were not inspected:

- `artifacts/specs/1061-blobstore-spec.mdx` N5/N6 nodes beyond the grep excerpt — full spec text not read; key identifiers (N5=TG ingest, N6=Discord ingest, V3/V4 slices) were found via grep.
- `artifacts/analyses/1277-stage-axis-refactor-strategy.mdx` §6 (inbound) — the §6 subsection was not directly read; §11 references and the absorbed-issues table (lines 330, 382) were read.
- `discord_formatting.py` `extract_attachments` — not read; inferred from `discord_normalize.py:83` call site.
- `src/lyra/adapters/shared/` files beyond `_shared.py` (push_to_hub_guarded signature) — not read.
- `src/lyra/core/hub/pipeline/message_pipeline.py` full content — only `_DROP` sentinel was checked.
