# Voice Schema Validation Fix — 2026-04-23

## Symptoms

- User reported: "⚠️ Voice synthesis temporarily unavailable"
- Telegram voice messages: no transcript, no error, no response
- Hub health: OK, all circuits closed
- All processes: RUNNING (lyra_hub, lyra_telegram, lyra_discord, voicecli_tts, voicecli_stt)

## Investigation

### Phase 1 — TTS Unavailable

Hub logs:
```
WARNING lyra.core.tts_dispatch: TTS adapter unavailable for msg id=...
WARNING lyra.nats.nats_tts_client: TTS: all workers unresponsive, last error type=TimeoutError
```

TTS worker logs:
```
DaemonUnavailableError: voicecli daemon not available for engine 'qwen-fast'
```

**Cause:** voiceCLI PR #95 fix for daemon fallback wasn't deployed (old process running).

**Action:** Restarted `voicecli_tts` — resolved daemon fallback issue.

### Phase 2 — STT Schema Validation Failure

After TTS restart, still failing. Hub logs:
```
WARNING lyra.core.hub.middleware_stt: STT unavailable for msg id=...: STT reply failed schema validation
```

**Root Cause:** `roxabi_contracts.ContractEnvelope` requires `trace_id` and `issued_at`, but voiceCLI's `build_reply()` only provided `contract_version`, `ok`, `request_id`.

#### Contract Envelope Schema

```python
# roxabi-contracts/src/roxabi_contracts/envelope.py
class ContractEnvelope(BaseModel):
    contract_version: str
    trace_id: Annotated[str, StringConstraints(min_length=1)]  # REQUIRED
    issued_at: datetime                                         # REQUIRED
```

#### voiceCLI's Old Implementation

```python
# voiceCLI/src/voicecli/nats/reply.py
def build_reply(*, ok: bool, request_id: str, **fields) -> dict:
    return {**fields, "contract_version": "1", "ok": ok, "request_id": request_id}
    # Missing: trace_id, issued_at
```

## Fix Applied

### Files Modified

- `voiceCLI/src/voicecli/nats/stt_adapter.py`
- `voiceCLI/src/voicecli/nats/tts_adapter.py`

### Changes

1. Added imports:
```python
from roxabi_contracts.voice import SttResponse  # or TtsResponse
from datetime import datetime, timezone
```

2. Removed old reply imports:
```python
# Removed: from voicecli.nats.reply import build_reply, encode_reply
```

3. Replaced `encode_reply(build_reply(...))` with proper Pydantic models:
```python
# Error response
SttResponse(
    contract_version=payload.get("contract_version", "1"),
    trace_id=payload.get("trace_id", request_id),
    issued_at=datetime.now(timezone.utc),
    ok=False,
    request_id=request_id,
    error="..."
).model_dump_json().encode()

# Success response
SttResponse(
    contract_version=payload.get("contract_version", "1"),
    trace_id=payload.get("trace_id", request_id),
    issued_at=datetime.now(timezone.utc),
    ok=True,
    request_id=request_id,
    text=result.text,
    language=result.language,
    duration_seconds=duration_seconds,
).model_dump_json().encode()
```

## Status

- ✅ Hotfix applied to production (live patch)
- ⚠️ NOT committed to voiceCLI repository
- ⚠️ Needs proper SDK integration

## Recommended Follow-up

1. **Commit fix to voiceCLI** — create PR with the patched adapters
2. **Add response builder to roxabi-contracts** — helper function that takes request payload and fields, returns properly constructed response
3. **Update voiceCLI to depend on roxabi-contracts** — use SDK models instead of custom `build_reply`

---

## Parallel Investigation — NATS ACL & Smoke Test (Claude Code)

### Tests Performed

| Test | Result | Detail |
|---|---|---|
| **TTS round-trip** | ✅ SUCCESS | 372KB audio via `lyra.voice.tts.request.{worker_id}` |
| **STT heartbeat listen** | ❌ NO DATA | No heartbeats received from voicecli_stt during 2s window |
| **STT processing** | ✅ WORKING | Logs show French transcription: "pas sûr que ça marche" |

### Additional Issues Found

#### 1. Voice Smoke Test — ACL Subject Mismatch

The smoke test (`lyra voice-smoke`) uses:
- Base subject: `lyra.voice.tts.request`
- No `inbox_prefix` set → random `_inbox.XXX.*`

But NATS ACL only allows:
- `lyra.voice.tts.request.>` (requires trailing token — NATS `>` matches 1+ tokens)
- `_INBOX.hub.>` for hub identity

**Result:** Smoke test fails with permissions violation even when TTS works correctly via per-worker subjects.

**Fix:** Update smoke test to:
1. Set `inbox_prefix="_INBOX.hub"` in `nats_connect()`
2. Subscribe to heartbeats first, then use per-worker subject `lyra.voice.tts.request.{worker_id}`

#### 2. lyra_hub Supervisor Config — Missing TLS CA

```ini
# voicecli_tts.conf has it:
environment=...,NATS_CA_CERT="/etc/nats/certs/ca.crt",...

# lyra_hub.conf is missing it:
environment=...,NATS_NKEY_SEED_PATH="%(ENV_HOME)s/.lyra/nkeys/hub.seed",...
# Missing: NATS_CA_CERT="/etc/nats/certs/ca.crt"
```

**Impact:** Hub can't verify TLS cert → SSL verification errors on startup.

#### 3. VRAM Constraints (RTX 3080 — 10GB)

| Process | VRAM |
|---|---|
| voicecli_stt | ~2.3 GB |
| voicecli_tts | ~4.8 GB (qwen-fast) |
| **Used** | **7.2 GB** |
| **Free** | **2.6 GB** |

TTS heartbeat shows `model_loaded: 'qwen-fast'` but config had `LYRA_TTS_ENGINE="chatterbox"`. Both engines appear to be loading at different times, causing VRAM exhaustion.

#### 4. Early Connection Errors (Red Herring?)

Error logs showed on STT restart:
```
INFO:roxabi_nats.connect:NATS TLS enabled (CA from NATS_CA_CERT)
ERROR:roxabi_nats.connect:NATS error: nats: permissions violation for subscription to "_inbox.iga4mnx3todutsuurtjoxq.*"
```

First connection has TLS but **no nkey auth** (missing `NATS nkey auth enabled` log). Subsequent reconnects show both TLS and nkey working.

**Hypothesis:** Race condition on first connection — env vars or seed file read timing. However, manual test with correct `inbox_prefix="_INBOX.voice-stt"` works perfectly, producing subscription `_INBOX.voice-stt.{NUID}.*` which matches ACL.

**Likely cause:** This was observed **before** the schema validation hotpatch. The patch may have also fixed connection behavior as a side effect (since adapters were replaced entirely).

### Working TTS Test Code

```python
import asyncio, json, base64
from uuid import uuid4
from roxabi_nats.connect import nats_connect

async def test():
    nc = await nats_connect('tls://127.0.0.1:4222', inbox_prefix='_INBOX.hub')

    # Get worker from heartbeat
    workers = []
    async def on_hb(msg):
        workers.append(json.loads(msg.data).get('worker_id'))
    sub = await nc.subscribe('lyra.voice.tts.heartbeat', cb=on_hb)
    await asyncio.sleep(2)
    await sub.unsubscribe()

    # Per-worker request
    subject = f'lyra.voice.tts.request.{workers[0]}'
    payload = json.dumps({
        'contract_version': '1',
        'request_id': str(uuid4()),
        'text': 'Hello world',
        'chunked': True,
    }).encode()

    reply = await nc.request(subject, payload, timeout=30)
    data = json.loads(reply.data)
    audio = base64.b64decode(data['audio_b64'])
    print(f'Success: {len(audio)} bytes audio')

    await nc.drain(); await nc.close()

asyncio.run(test())
```

### Summary

| Issue | Root Cause | Status |
|---|---|---|
| **Schema validation** | Missing `trace_id`/`issued_at` in responses | ✅ Hot-patched |
| **Smoke test ACL** | Base subject + no inbox_prefix | Needs code fix |
| **Hub TLS CA** | Missing env var in supervisor conf | Needs config fix |
| **VRAM** | Both engines loading | Config investigation |
| **Early conn error** | Race? Pre-patch artifact? | May be resolved by hotpatch |

---

## Reference

- `roxabi-contracts/src/roxabi_contracts/voice/testing.py` — shows correct pattern (FakeSttWorker)
- `roxabi-contracts/src/roxabi_contracts/envelope.py` — ContractEnvelope definition
- `lyra/src/lyra/cli_voice_smoke.py` — smoke test (needs inbox_prefix + per-worker subjects)
- `/etc/nats/nkeys/auth.conf` — live NATS ACL with correct pubkeys
