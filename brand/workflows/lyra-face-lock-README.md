# Lyra Face Lock — ComfyUI PuLID Flux2 Workflow

Face identity locking for Lyra avatar variations using `iFayens/ComfyUI-PuLID-Flux2`.
Face reference: `brand/concepts/avatar-final/006-just-solved-1024.png`.

> **Platform:** Machine 2 (Pop!_OS, RTX 5070 Ti, 16 GB VRAM)
> ⚠️ **ComfyUI and imageCLI must NEVER run concurrently — both use flux2-klein-4B on the same 16 GB GPU**

---

## Node Chain

```
DualCLIPLoader (T5-XXL + CLIP-L)
  └─→ CLIPTextEncodeFlux
        ↓ conditioning
CheckpointLoaderSimple (flux2-klein-4B)
  └─→ [model]
LoadImage (006-just-solved-1024.png)
  └─→ ApplyPulidFlux2 ←── [model] ←── CLIPTextEncodeFlux
        ↓ model (PuLID-injected)
FluxGuidance (guidance=1.0)  +  BasicScheduler (steps=8, sigma_max=...)
  └─→ SamplerCustomAdvanced
        ↓ latent
VAEDecode
  └─→ SaveImage → output/face-locked/
```

> ⚠️ **Do NOT use `KSampler` or single `CLIPTextEncode` — these are SDXL nodes, not Flux2**
> Flux2-klein uses a dual CLIP encoder (T5-XXL + CLIP-L) and the `SamplerCustomAdvanced` path.

---

## PuLID Settings

| Parameter | Value | Effect |
|-----------|-------|--------|
| `strength` | **0.6** | Recommended — balanced fidelity vs prompt adherence |
| `method` | `fidelity` | Strong identity lock (alternatives: `neutral`, `style`) |
| `start_at` | `0.0` | Inject from first denoising step |
| `end_at` | `1.0` | Inject through all steps (reduce to `0.6` for more pose freedom) |

### Tuning trade-offs

| Scenario | Adjustment |
|----------|-----------|
| Face drifts / not recognizable | Increase `strength` → 0.8, keep `method: fidelity` |
| Prompt adherence too low | Reduce `end_at` → 0.6–0.7, or switch `method: neutral` |
| Pose too stiff (mirrors reference) | Reduce `end_at` → 0.5–0.6, try `method: style` |
| Heavy stylization (anime/illustration) | `method: style`, `strength` 0.4–0.5 |

> Note: actual tuning values from quality gate (Slice 2 / #422) will be recorded here after first run.

---

## VRAM Budget

- `flux2-klein-4B` base: ~12 GB
- EVA02-CLIP-L (PuLID face encoder): ~1 GB
- PuLID safetensors: ~0.5 GB
- Activation peaks during identity injection: ~1.5 GB
- **Total: ~14–16 GB** (at Machine 2 ceiling)

**Machine 1 (RTX 3080, 10 GB): cannot run this workload.**

### OOM mitigations

1. Cap resolution at **1024×1024** (never go higher without testing)
2. Enable `--lowvram` flag when launching ComfyUI
3. Do not run imageCLI concurrently (GPU mutex — see warning above)
4. FP8/GGUF Klein weights in ComfyUI may reduce VRAM ~1–2 GB (unverified as of 2026-03-25)
5. Disable `torch.compile` in ComfyUI settings

---

## Setup (Machine 2 — Pop!_OS)

### PyTorch nightly (Blackwell sm_120)

```bash
# RTX 5070 Ti requires PyTorch nightly cu128 — stable PyTorch does NOT support sm_120
# DO NOT install xformers — it silently downgrades PyTorch to a non-sm_120 version
pip install --pre torch torchvision torchaudio \
  --index-url https://download.pytorch.org/whl/nightly/cu128

# Record after install:
# [RECORD AFTER INSTALL: pip show torch | grep Version]
```

> First-run PTX JIT compilation: **30–45 minutes** — normal, only happens once.

### ComfyUI

```bash
git clone https://github.com/comfyanonymous/ComfyUI ~/ComfyUI
cd ~/ComfyUI && pip install -r requirements.txt
# DO NOT: pip install xformers
```

### flux2-klein checkpoint (symlink from imageCLI cache)

```bash
ln -s ~/.cache/huggingface/hub/models--black-forest-labs--FLUX.2-klein-base-4B \
  ~/ComfyUI/models/checkpoints/flux2-klein-4B
```

### PuLID Flux2 custom node

Install via ComfyUI Manager (browser at `localhost:8188`):
→ Manager → Install Custom Nodes → search `iFayens/ComfyUI-PuLID-Flux2`

### AntelopeV2 (InsightFace face model — must be downloaded manually)

ComfyUI Manager does NOT auto-fetch AntelopeV2. Download manually:

```python
import os
from huggingface_hub import snapshot_download

snapshot_download(
    "deepinsight/insightface",
    repo_type="model",
    local_dir=os.path.expanduser("~/.insightface/models/antelopev2")
)
```

Verify: `ls ~/.insightface/models/antelopev2/`
Alt path (if ComfyUI looks here): `~/ComfyUI/models/insightface/antelopev2/`

---

## Workflow file

`brand/workflows/lyra-face-lock.json` — added in Slice 2 (#422) after quality gate passes.

---

## Prompt patterns

Face-lock prompts live in `brand/prompts/avatar-face-locked/`.

**Rule:** describe expression, lighting, background — **never describe facial features** (eye shape, jaw, bone structure, nose). Leave face structure un-described so it binds tightly to the reference image.

Example:
```
Editorial portrait photograph. Young woman, mid-twenties. Calm expression, slight focus.
Soft studio key light from the left, cool blue-grey. Obsidian background (#0a0a0f).
Head and upper shoulders, shallow DOF. Photorealistic, natural skin.
```

---

## See also

- `brand/AVATAR-PLAYBOOK.md` § 13 — Face Locking
- `brand/prompts/avatar-face-locked/` — prompt files with PuLID frontmatter
- Analysis: `artifacts/analyses/419-pulid-flux2-face-locking-analysis.mdx`
- Issue: #419 (parent), #421 (setup), #422 (workflow), #423 (campaign)
