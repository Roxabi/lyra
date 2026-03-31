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

## Quick Reference — Correct Files to Load

| Role | File | Why |
|---|---|---|
| Diffusion model | `models/diffusion_models/flux2-klein-4b-comfy.safetensors` | **Not** the HF symlink — ComfyUI needs different key names |
| Text encoder | `models/text_encoders/flux2-klein-qwen3.safetensors` | **Not** the shards — DualCLIPLoader has no FLUX2 dual-file handler |
| VAE | `models/vae/flux2-klein-vae.safetensors` | Direct symlink, no conversion needed |

## Starting ComfyUI

```bash
cd ~/ComfyUI
venv/bin/python main.py --listen 127.0.0.1 --port 8188
# GUI: http://localhost:8188
```

Or via the Lyra Makefile shortcut:

```bash
cd ~/projects/lyra
make comfyui          # start
make comfyui logs     # tail output
```

---

## Installation Playbook (reproduce from scratch)

Full step-by-step to rebuild this setup on a fresh Pop!_OS machine with an RTX 5070 Ti.

### Prerequisites

- RTX 5070 Ti (or other Blackwell GPU)
- Pop!_OS / Ubuntu with CUDA driver ≥ 570
- HF cache already populated with FLUX.2-klein-4B (via `imageCLI` or manual download)
  - Cache path: `~/.cache/huggingface/hub/models--black-forest-labs--FLUX.2-klein-4B/snapshots/e7b7dc27f91deacad38e78976d1f2b499d76a294/`

### Step 1 — Install CUDA 13.0 toolkit

cu130 PyTorch requires the CUDA 13.0 toolkit (in addition to whatever driver is installed).

```bash
sudo apt-get install -y cuda-toolkit-13-0
# Verify:
ls /usr/local/cuda-13.0/lib64/libcudart.so.13
```

### Step 2 — Clone ComfyUI and create venv

```bash
cd ~
git clone https://github.com/comfyanonymous/ComfyUI.git
cd ComfyUI
python3.12 -m venv venv
source venv/bin/activate
```

### Step 3 — Install PyTorch cu130 (Blackwell nightly)

```bash
pip install torch==2.12.0.dev20260331+cu130 \
            torchvision==0.27.0.dev20260331+cu130 \
            torchaudio==2.11.0.dev20260331+cu130 \
            --index-url https://download.pytorch.org/whl/nightly/cu130

# Verify Blackwell is detected:
python -c "import torch; print(torch.cuda.get_device_name(0))"
# Expected: NVIDIA GeForce RTX 5070 Ti
```

> **Why cu130, not cu128?** `comfy.quant_ops` uses optimized CUDA kernels that require the CUDA 13.0 toolkit headers at compile time. cu128 builds fail to load on Blackwell SM_120.

### Step 4 — Install ComfyUI dependencies

```bash
pip install -r requirements.txt
```

### Step 5 — Install custom nodes

```bash
cd ~/ComfyUI/custom_nodes

# ComfyUI Manager
git clone https://github.com/ltdrdata/ComfyUI-Manager.git

# PuLID Flux2
git clone https://github.com/iFayens/ComfyUI-PuLID-Flux2.git
```

### Step 6 — Install PuLID Flux2 dependencies

```bash
pip install insightface>=0.7.3 \
            onnxruntime-gpu>=1.16.0 \
            open-clip-torch>=3.2.0 \
            "numpy<2.0.0" \
            ml_dtypes==0.3.2 \
            opencv-python
```

> `numpy<2.0.0` and `ml_dtypes==0.3.2` are pinned — newer versions break InsightFace and open_clip compatibility.

### Step 7 — Symlink FLUX.2-klein model files

```bash
HF_SNAP=~/.cache/huggingface/hub/models--black-forest-labs--FLUX.2-klein-4B/snapshots/e7b7dc27f91deacad38e78976d1f2b499d76a294

mkdir -p ~/ComfyUI/models/{diffusion_models,vae,text_encoders}

# Diffusion model (HF format — will be converted in Step 8)
ln -s $HF_SNAP/transformer/diffusion_pytorch_model.safetensors \
      ~/ComfyUI/models/diffusion_models/flux2-klein-4b.safetensors

# VAE (direct symlink — no conversion needed)
ln -s $HF_SNAP/vae/diffusion_pytorch_model.safetensors \
      ~/ComfyUI/models/vae/flux2-klein-vae.safetensors

# Text encoder shards (will be merged in Step 9)
ln -s $HF_SNAP/text_encoder/model-00001-of-00002.safetensors \
      ~/ComfyUI/models/text_encoders/flux2-klein-qwen3-00001-of-00002.safetensors
ln -s $HF_SNAP/text_encoder/model-00002-of-00002.safetensors \
      ~/ComfyUI/models/text_encoders/flux2-klein-qwen3-00002-of-00002.safetensors
```

### Step 8 — Convert diffusion model (HF → ComfyUI format)

ComfyUI expects different key names than HF diffusers:
- Separate `to_q/to_k/to_v` → merged `qkv`
- `transformer_blocks` → `double_blocks`
- `x_embedder` → `img_in`
- `*.linear.weight` → `*.lin.weight` (modulation layers)

Run the conversion script (tracked at `brand/scripts/convert_flux2_klein_hf_to_comfy.py`):

```bash
cd ~/ComfyUI
cp ~/projects/lyra/brand/scripts/convert_flux2_klein_hf_to_comfy.py .
venv/bin/python convert_flux2_klein_hf_to_comfy.py
# Output: models/diffusion_models/flux2-klein-4b-comfy.safetensors (~7.2 GB)
# Takes ~5 min. Runs once only.
```

### Step 9 — Merge Qwen3 text encoder shards

`DualCLIPLoader` with `flux2` type falls through to `SDXLClipModel` (no FLUX2 dual-file handler in ComfyUI). The Qwen3 4B encoder must be loaded as a single file. Merge once:

```bash
cd ~/ComfyUI
venv/bin/python - <<'EOF'
from safetensors.torch import load_file, save_file
sd1 = load_file("models/text_encoders/flux2-klein-qwen3-00001-of-00002.safetensors")
sd2 = load_file("models/text_encoders/flux2-klein-qwen3-00002-of-00002.safetensors")
save_file({**sd1, **sd2}, "models/text_encoders/flux2-klein-qwen3.safetensors")
print("Done — 7.5 GB")
EOF
# Output: models/text_encoders/flux2-klein-qwen3.safetensors (~7.5 GB)
```

### Step 10 — Download AntelopeV2 (InsightFace face detector)

```bash
mkdir -p ~/ComfyUI/models/insightface/models/antelopev2

# Download 5 .onnx files from MonsterMMORPG/InstantID_Models on HF:
cd ~/ComfyUI/models/insightface/models/antelopev2
for f in 1k3d68.onnx 2d106det.onnx genderage.onnx glintr100.onnx scrfd_10g_bnkps.onnx; do
  wget "https://huggingface.co/MonsterMMORPG/InstantID_Models/resolve/main/antelopev2/$f"
done
```

### Step 11 — Download PuLID Flux2 Klein weights

```bash
mkdir -p ~/ComfyUI/models/pulid
cd ~/ComfyUI/models/pulid

# v2 recommended
wget "https://huggingface.co/Fayens/Pulid-Flux2/resolve/main/pulid_flux2_klein_v2.safetensors"
wget "https://huggingface.co/Fayens/Pulid-Flux2/resolve/main/pulid_flux2_klein_v1.safetensors"
```

### Step 12 — Run smoke test

```bash
cd ~/ComfyUI
cp ~/projects/lyra/brand/scripts/smoke_test_421.py .
venv/bin/python smoke_test_421.py
```

Expected output:
```
✓ Smoke test PASSED — flux2-klein-4B generated without OOM
  Peak VRAM: ~13.0 GB
```

> **First run only:** Blackwell JIT compilation triggers on first CUDA kernel call. Expect **30–45 min** before sampling starts. Subsequent runs are instant (kernels cached in `~/.cache/torch/kernels/`).

---

## Model File Summary

| ComfyUI path | Source | Size |
|---|---|---|
| `models/diffusion_models/flux2-klein-4b.safetensors` | HF symlink (do not use in ComfyUI) | 7.3 GB |
| `models/diffusion_models/flux2-klein-4b-comfy.safetensors` | converted — **use this** | 7.2 GB |
| `models/vae/flux2-klein-vae.safetensors` | HF symlink | 161 MB |
| `models/text_encoders/flux2-klein-qwen3-00001-of-00002.safetensors` | HF symlink shard 1 | 4.7 GB |
| `models/text_encoders/flux2-klein-qwen3-00002-of-00002.safetensors` | HF symlink shard 2 | 2.9 GB |
| `models/text_encoders/flux2-klein-qwen3.safetensors` | merged shards — **use this** | 7.5 GB |
| `models/insightface/models/antelopev2/` | downloaded | ~408 MB |
| `models/pulid/pulid_flux2_klein_v2.safetensors` | downloaded | 1.27 GB |
| `models/pulid/pulid_flux2_klein_v1.safetensors` | downloaded | 1.27 GB |

## Custom Nodes

| Node | Path | Notes |
|---|---|---|
| ComfyUI-Manager | `custom_nodes/ComfyUI-Manager/` | v0.30.4 |
| ComfyUI-PuLID-Flux2 | `custom_nodes/ComfyUI-PuLID-Flux2/` | iFayens/ComfyUI-PuLID-Flux2 |

## ComfyUI Node Setup (Flux2-Klein + PuLID workflow)

Load order in the UI:
1. **Load Diffusion Model** → `flux2-klein-4b-comfy.safetensors`
2. **Load CLIP** (type: `flux2`) → `flux2-klein-qwen3.safetensors`
3. **Load VAE** → `flux2-klein-vae.safetensors`
4. **Load InsightFace (PuLID)** → auto-detects AntelopeV2
5. **Load EVA-CLIP (PuLID)** → downloads automatically on first run (~800 MB)
6. **Load PuLID ✦ Flux.2** → `pulid_flux2_klein_v2.safetensors` (recommended)
7. **Apply PuLID ✦ Flux.2** → strength 1.4 recommended
8. **EmptyFlux2LatentImage + Flux2Scheduler → KSampler → VAEDecode → SaveImage**

## Blackwell JIT Cache

PyTorch caches compiled SM_120 CUDA kernels in `~/.cache/torch/kernels/`. Once compiled, all subsequent runs skip the 30–45 min wait.

**On this machine (ROXABITOWER):** JIT compiled on 2026-03-31 during setup session — cache is warm. Smoke test ran in ~1 sec, peak VRAM 13.0 GB.

On a **fresh machine**, the first generation will still hit the 30–45 min wait. To skip it on a new machine, copy the cache:
```bash
rsync -av ~/.cache/torch/kernels/ new-machine:~/.cache/torch/kernels/
```
