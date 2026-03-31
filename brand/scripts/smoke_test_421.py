"""Smoke test for ComfyUI + flux2-klein-4B — issue #421."""
import sys

import comfy.model_management  # noqa: F401 — side-effect: registers CUDA device + memory manager
import comfy.sd
import comfy.utils
import folder_paths
import torch
from comfy_extras.nodes_custom_sampler import (
    BasicGuider,
    KSamplerSelect,
    RandomNoise,
    SamplerCustomAdvanced,
)
from comfy_extras.nodes_flux import (
    EmptyFlux2LatentImage,
    Flux2Scheduler,
    FluxGuidance,
)
from nodes import SaveImage, VAEDecode

UNET = folder_paths.get_full_path(
    "diffusion_models", "flux2-klein-4b-comfy.safetensors"
)
TE = folder_paths.get_full_path("text_encoders", "flux2-klein-qwen3.safetensors")
VAE_P = folder_paths.get_full_path("vae", "flux2-klein-vae.safetensors")

for _label, _path in [("UNET", UNET), ("TE", TE), ("VAE", VAE_P)]:
    if _path is None:
        print(f"ERROR — model file not found for {_label}", file=sys.stderr)
        sys.exit(1)

try:
    print("Loading text encoder (Qwen3 4B, merged)…")
    clip = comfy.sd.load_clip(
        ckpt_paths=[TE],
        clip_type=comfy.sd.CLIPType.FLUX2,
        embedding_directory=folder_paths.get_folder_paths("embeddings"),
    )
    print("  TE loaded:", type(clip.cond_stage_model).__name__)

    print("Loading diffusion model (ComfyUI format)…")
    unet = comfy.sd.load_diffusion_model(UNET)
    print("  UNET loaded:", type(unet.model).__name__)

    print("Loading VAE…")
    sd_vae = comfy.utils.load_torch_file(VAE_P)
    vae = comfy.sd.VAE(sd=sd_vae)
    print("  VAE loaded:", type(vae).__name__)

    print("Encoding prompt…")
    PROMPT = "a white cat on a red chair"
    tokens = clip.tokenize(PROMPT)
    cond = clip.encode_from_tokens_scheduled(tokens, add_dict={"guidance": 3.5})

    print("Applying FluxGuidance…")
    guided_cond = FluxGuidance.execute(conditioning=cond, guidance=3.5)[0]

    print("Building guider…")
    guider = BasicGuider.execute(model=unet, conditioning=guided_cond)[0]

    print("Creating latent (512×512)…")
    latent = EmptyFlux2LatentImage.execute(width=512, height=512, batch_size=1)[0]

    print("Building sigmas (10 steps)…")
    sigmas = Flux2Scheduler.execute(steps=10, width=512, height=512)[0]

    print("Building noise + sampler…")
    noise = RandomNoise.execute(noise_seed=42)[0]
    sampler = KSamplerSelect.execute(sampler_name="euler")[0]

    print("Sampling…  (first run triggers Blackwell JIT — may take 30–45 min)")
    samples = SamplerCustomAdvanced.execute(
        noise=noise, guider=guider, sampler=sampler,
        sigmas=sigmas, latent_image=latent,
    )[0]

    print("Decoding…")
    images = VAEDecode().decode(samples=samples, vae=vae)[0]
    print(f"  Output shape: {images.shape}")
    assert images.shape == (1, 512, 512, 3), f"Unexpected output shape: {images.shape}"

    print("Saving…")
    result = SaveImage().save_images(images=images, filename_prefix="smoke_test_421")
    assert result and result.get("ui", {}).get("images"), "SaveImage returned no files"

    peak_vram = torch.cuda.max_memory_allocated() / 1024**3
    VRAM_CEILING_GB = 20.0
    assert peak_vram < VRAM_CEILING_GB, (
        f"VRAM regression: {peak_vram:.1f} GB >= {VRAM_CEILING_GB} GB"
    )
    print("\n✓ Smoke test PASSED — flux2-klein-4B generated without OOM")
    print(f"  Peak VRAM: {peak_vram:.1f} GB")
except Exception as exc:
    print(f"ERROR: {exc}", file=sys.stderr)
    sys.exit(1)
sys.exit(0)
