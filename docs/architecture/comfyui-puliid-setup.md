# ComfyUI + PuLID Flux2 Setup — Machine 2 (Pop!_OS)

Setup for issue [#421](https://github.com/Roxabi/lyra/issues/421).

## Environment

| | Value |
|--|-------|
| Host | ROXABITOWER (Machine 2) |
| GPU | RTX 5070 Ti (Blackwell, sm_120) |
| CUDA toolkit | 12.8 + 13.0 (both installed) |
| CUDA driver | 580.126.18 |
| Python | 3.12 |
| Install path | `~/ComfyUI/` |

## PyTorch (cu130)

```
torch==2.12.0.dev20260331+cu130
torchvision==0.27.0.dev20260331+cu130
torchaudio==2.11.0.dev20260331+cu130
index-url: https://download.pytorch.org/whl/nightly/cu130
```

> cu130 required for `comfy.quant_ops` optimized CUDA kernels on Blackwell.
> CUDA 13.0 toolkit (`cuda-toolkit-13-0`) must be installed alongside cu128.

## Model Symlinks (no duplication from imageCLI HF cache)

All flux2-klein weights are symlinked from `~/.cache/huggingface/hub/models--black-forest-labs--FLUX.2-klein-4B/snapshots/e7b7dc27f91deacad38e78976d1f2b499d76a294/`:

| ComfyUI path | Source |
|---|---|
| `models/diffusion_models/flux2-klein-4b.safetensors` | `transformer/diffusion_pytorch_model.safetensors` (7.3 GB) |
| `models/vae/flux2-klein-vae.safetensors` | `vae/diffusion_pytorch_model.safetensors` (161 MB) |
| `models/text_encoders/flux2-klein-qwen3-00001-of-00002.safetensors` | `text_encoder/model-00001-of-00002.safetensors` (4.7 GB) |
| `models/text_encoders/flux2-klein-qwen3-00002-of-00002.safetensors` | `text_encoder/model-00002-of-00002.safetensors` (2.9 GB) |

## Custom Nodes

| Node | Path | Notes |
|---|---|---|
| ComfyUI-Manager | `custom_nodes/ComfyUI-Manager/` | v0.30.4 |
| ComfyUI-PuLID-Flux2 | `custom_nodes/ComfyUI-PuLID-Flux2/` | iFayens/ComfyUI-PuLID-Flux2 |

### PuLID Flux2 Dependencies

```
insightface>=0.7.3
onnxruntime-gpu>=1.16.0
open-clip-torch>=3.2.0
numpy<2.0.0          # pinned: InsightFace + open_clip compat
ml_dtypes==0.3.2     # pinned: AttributeError otherwise
opencv-python
```

> EVA-CLIP (~800 MB) downloads automatically on first PuLID run.

## Weights

| Model | Path | Source |
|---|---|---|
| AntelopeV2 | `models/insightface/models/antelopev2/` | MonsterMMORPG/InstantID_Models (5 .onnx files, ~408 MB) |
| PuLID Flux2 Klein v1 | `models/pulid/pulid_flux2_klein_v1.safetensors` | Fayens/Pulid-Flux2 (1.27 GB) |
| PuLID Flux2 Klein v2 | `models/pulid/pulid_flux2_klein_v2.safetensors` | Fayens/Pulid-Flux2 (1.27 GB) |

## ComfyUI Node Setup (Flux2-Klein workflow)

Load order in the UI:
1. **Load Diffusion Model** → `flux2-klein-4b.safetensors`
2. **Dual CLIP Loader** (type: `flux2`) → both `flux2-klein-qwen3-*` shards
3. **Load VAE** → `flux2-klein-vae.safetensors`
4. **Load InsightFace (PuLID)** → auto-detects AntelopeV2
5. **Load EVA-CLIP (PuLID)** → downloads on first run
6. **Load PuLID ✦ Flux.2** → `pulid_flux2_klein_v2.safetensors` (recommended)
7. **Apply PuLID ✦ Flux.2** → strength 1.4 recommended
8. **EmptyFlux2LatentImage + Flux2Scheduler → KSampler → VAEDecode → SaveImage**

## Starting ComfyUI

```bash
cd ~/ComfyUI
venv/bin/python main.py --listen 127.0.0.1 --port 8188
# GUI: http://localhost:8188
```

## First-run Notes

- First generation triggers Blackwell JIT compilation: **expect 30–45 min**
- Subsequent runs are fast (~2–5 min for 50 steps at 1024×1024)
- EVA-CLIP downloads automatically on first PuLID use (~800 MB)
- Done when: flux2-klein loads without OOM, smoke-test image generates
