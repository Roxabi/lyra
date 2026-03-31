"""
Convert FLUX.2-klein-4B transformer weights from HF diffusers format
to ComfyUI native format.

HF format uses:
  - separate to_q, to_k, to_v  →  ComfyUI merges into qkv
  - transformer_blocks          →  double_blocks
  - single_transformer_blocks   →  single_blocks
  - x_embedder                  →  img_in
  - context_embedder            →  txt_in
  - *.linear.*                  →  *.lin.* (for modulation layers)
  - norm_out / proj_out         →  final_layer.adaLN_modulation.1 / final_layer.linear
  - time_guidance_embed.*       →  time_in.*

Input:  models/diffusion_models/flux2-klein-4b.safetensors
        (symlink to HF cache, 7.3 GB)
Output: models/diffusion_models/flux2-klein-4b-comfy.safetensors  (~7.3 GB)
"""
import os

import torch
from safetensors.torch import load_file, save_file

SRC = "models/diffusion_models/flux2-klein-4b.safetensors"
DST = "models/diffusion_models/flux2-klein-4b-comfy.safetensors"

print(f"Loading {SRC}…")
sd = load_file(SRC)
print(f"  {len(sd)} keys loaded")

out = {}

def add(dst_key, tensor):
    out[dst_key] = tensor

# ── simple renames ────────────────────────────────────────────────────────────
SIMPLE = {
    "x_embedder.weight": "img_in.weight",
    "context_embedder.weight": "txt_in.weight",
    "double_stream_modulation_img.linear.weight":
        "double_stream_modulation_img.lin.weight",
    "double_stream_modulation_txt.linear.weight":
        "double_stream_modulation_txt.lin.weight",
    "single_stream_modulation.linear.weight":
        "single_stream_modulation.lin.weight",
    "norm_out.linear.weight": "final_layer.adaLN_modulation.1.weight",
    "proj_out.weight": "final_layer.linear.weight",
    "time_guidance_embed.timestep_embedder.linear_1.weight":
        "time_in.in_layer.weight",
    "time_guidance_embed.timestep_embedder.linear_2.weight":
        "time_in.out_layer.weight",
}
for src_k, dst_k in SIMPLE.items():
    if src_k in sd:
        add(dst_k, sd[src_k])
        print(f"  {src_k} → {dst_k}")
    else:
        print(f"  MISSING: {src_k}")

# ── double blocks (transformer_blocks.N → double_blocks.N) ───────────────────
n_double = (
    max(int(k.split(".")[1]) for k in sd if k.startswith("transformer_blocks."))
    + 1
)
print(f"\nDouble blocks: {n_double}")

for n in range(n_double):
    p = f"transformer_blocks.{n}"
    dp = f"double_blocks.{n}"

    # image attention — merge separate q, k, v → qkv
    q = sd[f"{p}.attn.to_q.weight"]
    k = sd[f"{p}.attn.to_k.weight"]
    v = sd[f"{p}.attn.to_v.weight"]
    add(f"{dp}.img_attn.qkv.weight", torch.cat([q, k, v], dim=0))

    add(f"{dp}.img_attn.proj.weight",          sd[f"{p}.attn.to_out.0.weight"])
    add(f"{dp}.img_attn.norm.key_norm.weight",  sd[f"{p}.attn.norm_k.weight"])
    add(f"{dp}.img_attn.norm.query_norm.weight",sd[f"{p}.attn.norm_q.weight"])
    add(f"{dp}.img_mlp.0.weight",              sd[f"{p}.ff.linear_in.weight"])
    add(f"{dp}.img_mlp.2.weight",              sd[f"{p}.ff.linear_out.weight"])

    # text attention — merge separate add_q, add_k, add_v → qkv
    aq = sd[f"{p}.attn.add_q_proj.weight"]
    ak = sd[f"{p}.attn.add_k_proj.weight"]
    av = sd[f"{p}.attn.add_v_proj.weight"]
    add(f"{dp}.txt_attn.qkv.weight", torch.cat([aq, ak, av], dim=0))

    add(f"{dp}.txt_attn.proj.weight",          sd[f"{p}.attn.to_add_out.weight"])
    add(f"{dp}.txt_attn.norm.key_norm.weight",  sd[f"{p}.attn.norm_added_k.weight"])
    add(f"{dp}.txt_attn.norm.query_norm.weight",sd[f"{p}.attn.norm_added_q.weight"])
    add(f"{dp}.txt_mlp.0.weight",              sd[f"{p}.ff_context.linear_in.weight"])
    add(f"{dp}.txt_mlp.2.weight",              sd[f"{p}.ff_context.linear_out.weight"])

print(f"  converted {n_double} double blocks")

# ── single blocks (single_transformer_blocks.N → single_blocks.N) ────────────
n_single = (
    max(int(k.split(".")[1]) for k in sd if k.startswith("single_transformer_blocks."))
    + 1
)
print(f"Single blocks: {n_single}")

for n in range(n_single):
    p = f"single_transformer_blocks.{n}"
    sp = f"single_blocks.{n}"

    add(f"{sp}.linear1.weight",        sd[f"{p}.attn.to_qkv_mlp_proj.weight"])
    add(f"{sp}.linear2.weight",        sd[f"{p}.attn.to_out.weight"])
    add(f"{sp}.norm.key_norm.weight",  sd[f"{p}.attn.norm_k.weight"])
    add(f"{sp}.norm.query_norm.weight",sd[f"{p}.attn.norm_q.weight"])

print(f"  converted {n_single} single blocks")

# ── sanity check ─────────────────────────────────────────────────────────────
print(f"\nTotal output keys: {len(out)}")
# Verify all source keys are accounted for
src_keys_used = set(SIMPLE.keys())
for n in range(n_double):
    p = f"transformer_blocks.{n}"
    for suffix in ["attn.to_q.weight","attn.to_k.weight","attn.to_v.weight",
                   "attn.to_out.0.weight","attn.norm_k.weight","attn.norm_q.weight",
                   "ff.linear_in.weight","ff.linear_out.weight",
                   "attn.add_q_proj.weight","attn.add_k_proj.weight","attn.add_v_proj.weight",
                   "attn.to_add_out.weight","attn.norm_added_k.weight","attn.norm_added_q.weight",
                   "ff_context.linear_in.weight","ff_context.linear_out.weight"]:
        src_keys_used.add(f"{p}.{suffix}")
for n in range(n_single):
    p = f"single_transformer_blocks.{n}"
    for suffix in ["attn.to_qkv_mlp_proj.weight","attn.to_out.weight",
                   "attn.norm_k.weight","attn.norm_q.weight"]:
        src_keys_used.add(f"{p}.{suffix}")

unconverted = set(sd.keys()) - src_keys_used
if unconverted:
    print(f"WARNING — unconverted keys: {unconverted}")
else:
    print("✓ All source keys converted")

# ── save ─────────────────────────────────────────────────────────────────────
print(f"\nSaving to {DST}…")
save_file(out, DST)
size_gb = os.path.getsize(DST) / 1024**3
print(f"✓ Saved: {size_gb:.2f} GB")
print(
    "\nDone. Load in ComfyUI as 'Load Diffusion Model'"
    " → flux2-klein-4b-comfy.safetensors"
)
