# Lyra Supervisor Programs

Generated from `deploy/agents.yml` via `make gen-conf`. Do not edit `.conf` files directly — edit the yaml and regenerate. CI enforces drift-free state.

Loaded on **both** dev (roxabitower) and prod (roxabituwer) since both machines run `lyra.service`.

## Programs

| Program | Purpose | Notes |
|---------|---------|-------|
| `lyra_hub` | Hub process (NATS-connected) | Requires NATS |
| `lyra_telegram` | Telegram adapter | Requires NATS |
| `lyra_discord` | Discord adapter | Requires NATS |
| `lyra_stt` | STT NATS bridge in lyra's venv (calls voicecli worker) | Requires NATS + voicecli worker |
| `lyra_tts` | TTS NATS bridge in lyra's venv (calls voicecli worker) | Requires NATS + voicecli worker |

## What lives here vs. elsewhere (ADR-047 Pattern B)

This directory holds **lyra-owned supervisor programs only** — hub + platform adapters + voice bridges that run inside lyra's venv. External satellites own their own supervisor configs in their own repos and register on dev's hub supervisor (`~/projects/conf.d/`) via `make register`.

| Service | Lives in | Runs on |
|---------|----------|---------|
| `voicecli_stt` / `voicecli_tts` workers | `voiceCLI/supervisor/conf.d/` | dev + prod (registered on each hub) |
| `imagecli_gen` worker (ADR-050, imageCLI#50) | `imageCLI/supervisor/conf.d/` | dev only (VRAM: RTX 3080 on prod too small) |
| `forge`, `idna` | their own repos | dev only (local tooling) |

**Do not add satellite-owned workers to `agents.yml`.** Doing so ships a broken config to prod via git sync — the external binary won't exist in lyra's venv. This happened historically with `imagecli_gen` (removed 2026-04-21). See ADR-047 for the ownership pattern.
