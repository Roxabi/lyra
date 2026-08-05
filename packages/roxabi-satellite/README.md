# roxabi-satellite

Shared **NATS satellite plumbing** for Roxabi GPU worker CLIs (`voiceCLI`, `imageCLI`, `llmCLI`).

This is **not** a replacement for domain engines (Whisper, Qwen-TTS, FLUX, LiteLLM). Each CLI keeps its GPU logic locally and imports plumbing from here.

## What lives here

| Module | Role |
|---|---|
| `roxabi_satellite.blobs` | `HttpBlobStore` singleton, `blob_ref_to_contract`, ADR-068 env config |
| `roxabi_satellite.envelope` | `coerce_envelope_fields` for hub payloads missing WorkEnvelope fields |
| `roxabi_satellite.tokens` | `validate_nats_token` (public; no `roxabi_nats._*` imports) |
| `roxabi_satellite.errors` | `WorkerError` registries + `resolve_worker_error` |
| `roxabi_satellite.voice.validation` | STT/TTS ingress validation against `roxabi_contracts.voice` |
| `roxabi_satellite.voice.replies` | Wire-safe STT/TTS error reply bytes |

## Install (external CLIs)

```toml
[tool.uv.sources]
roxabi-satellite = {
  git = "https://github.com/Roxabi/roxabi-factory.git",
  subdirectory = "packages/roxabi-satellite",
  branch = "staging",
}
```

> **Why `branch`, not `tag`, here?** `roxabi-nats` and `roxabi-contracts` MUST
> be pinned by tag — but `roxabi-satellite` is the sanctioned exception. It is
> pre-tag plumbing (no `roxabi-satellite/*` tags yet), and depending on it
> pulls the whole SDK from one staging commit, so you get a coherent
> `contracts`+`nats`+`blobs` triple with no partial-upgrade risk. Full
> rationale: [Pin doctrine](../../docs/architecture/contracts.md#pin-doctrine-external-consumers).
> Never branch-pin `roxabi-nats`/`roxabi-contracts` directly.

```toml
[project.optional-dependencies]
nats = ["roxabi-satellite", "nats-py>=2.6,<3", "nkeys>=0.1"]
```

`roxabi-satellite` pulls `roxabi-contracts`, `roxabi-nats`, and `roxabi-blobs` transitively.

## voiceCLI migration (next step)

| Today (`voiceCLI`) | After (`roxabi-satellite`) |
|---|---|
| `voicecli.adapters.nats.blobs` | `roxabi_satellite.blobs` |
| `voicecli.adapters.nats._validation` | `roxabi_satellite.voice.validation` |
| `_err_stt` / `_err_tts` in adapters | `roxabi_satellite.voice.replies` |
| `voicecli.adapters.nats._validate` | `roxabi_satellite.tokens` |

Adapters (`TtsNatsAdapter`, `SttNatsAdapter`) and runners (`run_synthesis`, `run_transcription`) **stay in voiceCLI**.

Social media publishing is out of band (#2329): agents use the Postiz skill/CLI against
the Postiz Public API — no factory NATS satellite / `roxabi_satellite.socialmedia` module.

## image / llm

| Module | Role |
|---|---|
| `roxabi_satellite.image.replies` | `build_image_error_reply` |
| `roxabi_satellite.image.errors` | legacy code → `WorkerError` |
| `roxabi_satellite.image.delivery` | httpx/BlobStore error sanitization |
| `roxabi_satellite.llm.replies` | `build_llm_error_reply` (stream + blocking) |

Domain validation for image (LoRA paths, bounds) and LLM lifecycle stay in each CLI.

## Blobstore env conventions

| Consumer | Config source | Env vars |
|---|---|---|
| voiceCLI | `roxabi_satellite.blobs` | `BLOBSTORE_*` (ADR-068); Factory quadlet may set `FACTORY_BLOBSTORE_*` (aliased at startup) |
| imageCLI | `imagecli.nats.blobs` (local singleton) | `IMAGECLI_BLOBSTORE_*` / `imagecli.toml [blobstore]` — **not** ADR-068 names |

imageCLI keeps its own blobstore loader because operators already mount `imagecli-blobstore-token` and configure `IMAGECLI_BLOBSTORE_URL`. Use `roxabi_satellite.image.delivery` for httpx-safe errors; migrate to `roxabi_satellite.blobs` only if imageCLI adopts ADR-068 env names.
