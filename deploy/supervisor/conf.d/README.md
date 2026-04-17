# Production Supervisor Programs

This directory contains supervisor configs for **production only** (roxabituwer, RTX 3080 10GB).

## Programs

| Program | Purpose | Notes |
|---------|---------|-------|
| `lyra_hub` | Hub process (NATS-connected) | Requires NATS |
| `lyra_telegram` | Telegram adapter | Requires NATS |
| `lyra_discord` | Discord adapter | Requires NATS |
| `lyra_stt` | STT NATS adapter (voicecli) | Requires NATS + voicecli + RTX 3080 VRAM |
| `lyra_tts` | TTS NATS adapter (voicecli) | Requires NATS + voicecli + RTX 3080 VRAM |
| `voicecli_stt` | voicecli STT Unix-socket daemon | Optional inner daemon for lyra_stt |
| `voicecli_tts` | voicecli TTS Unix-socket daemon | Optional inner daemon for lyra_tts |

## Intentionally absent (local-only)

| Program | Reason |
|---------|--------|
| `imagecli_gen` | Requires >10GB VRAM, not available on RTX 3080 |
| `forge` | Local dev tooling only (diagram server) |
| `idna` | Local dev tooling only |

These programs run via the root supervisor hub (`~/projects/conf.d/`) on the local machine only.
