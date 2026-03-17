# Voice-to-Voice Models — State of the Art Analysis

> **Date**: 2026-03-17
> **Author**: Mickael + Claude
> **Status**: Research / Decision: Scenario D (hybrid split + fallback)
> **Context**: Evaluate voice-to-voice models for Lyra's voice capabilities

---

## Table of Contents

1. [Why Voice-to-Voice](#1-why-voice-to-voice)
2. [Three Architectures](#2-three-architectures)
3. [Model Catalog](#3-model-catalog)
4. [Tool Calling Comparison](#4-tool-calling-comparison)
5. [Hardware Requirements](#5-hardware-requirements)
6. [Deep Dive: Qwen Omni Family](#6-deep-dive-qwen-omni-family)
7. [Latency Analysis: Pipeline vs End-to-End](#7-latency-analysis-pipeline-vs-end-to-end)
8. [VRAM Cohabitation Analysis](#8-vram-cohabitation-analysis)
9. [Recommendation for Lyra](#9-recommendation-for-lyra)

---

## 1. Why Voice-to-Voice

A voice-to-voice (speech-to-speech) model processes audio input and produces audio output **natively**, without a cascaded STT → LLM → TTS pipeline.

**Key question**: Can these models use tools/plugins, or is it just conversation?

**Answer**: Most open-source models are purely conversational. Only the **Qwen Omni** family and closed-source models (GPT-4o realtime, Gemini 2.5 Flash) support function calling in voice mode. Without tool calling, a voice model is essentially a chatbot that speaks — it can't *do* anything.

---

## 2. Three Architectures

| Architecture | Principle | Tool Calling | Latency | Examples |
|---|---|---|---|---|
| **Native end-to-end** | Audio in → audio out directly, full-duplex | No (audio token budget limits context injection) | 80-205ms | Moshi, PersonaPlex, Hertz-Dev |
| **Thinker-Talker** | Model "thinks" in text space, then generates audio in parallel | **Yes** (via text-space reasoning) | 234-257ms | Qwen2.5-Omni, Qwen3-Omni |
| **Orchestrated pipeline** | STT → LLM → TTS (cascade) | **Yes** (via the LLM) | 1000-3000ms | LiveKit + Whisper + LLM + Orpheus |

**Key insight**: Tool calling requires the model to operate partially in text/hidden-state space. The Thinker-Talker architecture is the only open-source approach that achieves both natural voice AND tool use.

---

## 3. Model Catalog

### 3.1. End-to-End Native (Full-Duplex)

#### Moshi (Kyutai) — The Pioneer
- **Params**: 7B (Helium LLM + Mimi codec at 12.5Hz, 1.1 kbps)
- **Latency**: 160ms first response
- **Full-duplex**: Yes — true simultaneous listening and speaking
- **Tool calling**: No
- **VRAM**: 24 GB minimum (no PyTorch quantization support)
- **Quality**: Natural conversational rhythm but weak task adherence (1.26/5). Interruption handling: 60.6%
- **License**: CC-BY 4.0
- **Our hardware**: Does NOT run on RTX 5070 Ti (16GB) or RTX 3080 (12GB)

#### NVIDIA PersonaPlex-7B (January 2026)
- **Params**: 7B (built on Moshi backbone)
- **Latency**: 205ms
- **Full-duplex**: Yes — 100% interruption success rate
- **Tool calling**: No
- **VRAM**: 24 GB recommended (speech output ~18GB)
- **Quality**: Task adherence 4.34-4.40/5 (massive improvement over Moshi). Custom voices/personas via text+audio prompts
- **License**: MIT (code), NVIDIA Open Model License (weights)
- **Our hardware**: Does NOT run

#### Hertz-Dev (Standard Intelligence) — Fastest Latency
- **Params**: 8.5B
- **Latency**: 80ms theoretical, 120ms real-world on RTX 4090
- **Full-duplex**: Yes
- **Tool calling**: No — pure audio model, no LLM text integration
- **VRAM**: 24 GB
- **Note**: Base model, requires fine-tuning. Trained on 20M hours of audio
- **License**: Apache 2.0
- **Our hardware**: Does NOT run

#### Voila (Maitrix, May 2025)
- **Params**: ~7B
- **Latency**: 195ms
- **Full-duplex**: Yes
- **Tool calling**: Not documented
- **Features**: 1M+ pre-built voices, custom voice from 10s sample
- **License**: Open source

### 3.2. Thinker-Talker (Tool Calling Capable)

#### Qwen2.5-Omni-7B (Alibaba, March 2025) ★ Best fit for our hardware
- **See [Section 6](#6-deep-dive-qwen-omni-family) for deep dive**

#### Qwen3-Omni-30B-A3B (Alibaba, September 2025) ★ Best overall
- **See [Section 6](#6-deep-dive-qwen-omni-family) for deep dive**

#### GLM-4-Voice-9B (Zhipu AI)
- **Params**: 9B
- **VRAM**: ~10.5 GB FP16, ~2.6 GB INT4
- **Features**: Chinese + English, emotion/intonation/rate/dialect control
- **Tool calling**: Not documented
- **License**: Open source
- **Our hardware**: Runs on both RTX 5070 Ti and RTX 3080

### 3.3. Other Notable Models

| Model | Params | Latency | VRAM | Tool Calling | Notes |
|---|---|---|---|---|---|
| LLaMA-Omni2 | 0.5-32B | 226ms | Variable | No | Simultaneous text + speech generation |
| Ichigo (Jan) | ~8B | 111ms | ~19 GB | No | "Open-source on-device Siri" |
| Mini-Omni2 | ~7B | N/A | ~16 GB | No | Research/demo quality |
| VITA-1.5 | Large | N/A | Large | No | Outperforms pro ASR models |

### 3.4. High-Quality TTS (For Pipeline Approach)

| Model | Params | VRAM | Quality | License |
|---|---|---|---|---|
| **Sesame CSM-1B** | 1B | 4.5 GB | Most natural conversational speech | Apache 2.0 |
| **Orpheus TTS** | 150M-3B | 1-16 GB | Emotion tags (laugh, sigh...), voice cloning | Apache 2.0 |
| **Qwen3-TTS** | 0.6-1.7B | 4-8 GB | 97ms latency, 10 languages, 3s cloning | Apache 2.0 |
| **Dia2** (Nari Labs) | 1.6B | 7-10 GB | Multi-speaker, streaming | Apache 2.0 |
| **Kokoro** | 82M | <1 GB | Best quality-to-size ratio, CPU-capable | Open |
| **CosyVoice3** | 0.5B | ~1 GB | Streaming real-time, multilingual | Apache 2.0 |
| **Chatterbox** | 350-500M | 8 GB | 23+ languages | MIT |
| **Kani-TTS-2** | 400M | 3 GB | Voice cloning, very fast | Apache 2.0 |

### 3.5. Closed-Source (Reference)

| Model | Latency | Tool Calling | Key Strength |
|---|---|---|---|
| **OpenAI gpt-realtime** | <200ms | Yes (MCP, async function calls, SIP) | Most natural, best tool calling (66.5% accuracy) |
| **Gemini 2.5 Flash Native Audio** | Very low | Yes (mid-conversation function calling) | Real-time translation, multilingual |
| **Hume EVI 3/4** | <300ms | Via LLM backends (Claude 4, Gemini) | Best emotional expression |

---

## 4. Tool Calling Comparison

| Model | Tool Calling | How It Works |
|---|---|---|
| **OpenAI gpt-realtime** | Full | Async function calls, MCP servers, SIP, image inputs |
| **Gemini 2.5 Flash** | Full | Mid-conversation function calling, Google Search grounding |
| **Qwen3-Omni** | Full | Via Thinker's text domain; function calling from audio input |
| **Qwen2.5-Omni** | Yes | Via Thinker's text domain; RAG, long context natively |
| **Hume EVI 3/4** | Via LLM backends | Delegates to Claude 4, Gemini 2.5, etc. |
| **Moshi / PersonaPlex** | No | Audio token budgets limit context injection |
| **Hertz-Dev** | No | Pure audio model, no text/LLM integration |
| **All others** | No | Conversational or TTS-only |

---

## 5. Hardware Requirements

### Our Hardware

| Machine | GPU | VRAM | Role |
|---|---|---|---|
| ROXABITOWER (local) | RTX 5070 Ti | 16 GB | Dev + AI workloads |
| roxabituwer (prod) | RTX 3080 | 12 GB | Always-on hub |

### Model Fit Matrix

| Model | Min VRAM | RTX 3080 (12GB) | RTX 5070 Ti (16GB) | RTX 4090 (24GB) |
|---|---|---|---|---|
| Moshi 7B | 24 GB | No | No | Marginal |
| PersonaPlex 7B | 24 GB | No | No | Marginal |
| Hertz-Dev 8.5B | 24 GB | No | No | Yes |
| **Qwen2.5-Omni 7B (INT4)** | **~7-8 GB** | **Yes** | **Yes** | Yes |
| **Qwen2.5-Omni 3B** | **~4-5 GB** | **Yes** | **Yes** | Yes |
| Qwen3-Omni AWQ-4bit | ~20-25 GB | No | No | Marginal |
| GLM-4-Voice 9B (INT4) | ~2.6 GB | Yes | Yes | Yes |
| Sesame CSM 1B | 4.5 GB | Yes | Yes | Yes |
| Orpheus 3B | ~6-8 GB | Yes | Yes | Yes |
| Kokoro 82M | <1 GB | Yes (CPU) | Yes (CPU) | Yes |

---

## 6. Deep Dive: Qwen Omni Family

### 6.1. Architecture — Thinker-Talker

```
Audio 16kHz ──→ AuT Encoder (0.6B, 12.5Hz) ──→ ┐
Image/Video ──→ ViT Encoder ──────────────────→ ├──→ Thinker (reasoning) ──→ Talker (speech gen) ──→ Code2Wav ──→ Audio 24kHz
Text ─────────────────────────────────────────→ ┘         │
                                                          └──→ Text output (119 languages)
```

The **Thinker** reasons in text/hidden-state space (enabling tool calling). The **Talker** generates audio in streaming from multimodal features — NOT from the Thinker's text output. This decoupling allows independent control of content vs. voice style.

### 6.2. Qwen3-Omni-30B-A3B — Specs

| Component | Total Params | Active Params | Layers | Experts |
|---|---|---|---|---|
| **Thinker** | 30B | 3.3B | 48 | 128 (8 active/token) |
| **Talker** | 3B | 0.3B | 20 | 128 (6 active/token) |
| **AuT Encoder** | 0.6B | — | 32 | — |
| **Code2Wav** | 0.2B | — | — | — |

**AuT Encoder**: Replaces Whisper, trained from scratch on 20M hours of supervised audio data. Dynamic attention windows (1-8s). 12.5Hz token rate (one frame every 80ms).

**TMRoPE**: Position embedding factorized into temporal + height + width. Each modality uses different encoding — audio gets absolute timestamps at 80ms resolution, video gets per-frame spatial positions with monotonic temporal IDs.

**Audio Codec**: RVQ with 16 codebook groups (1 semantic @ 4096, 15 residual @ 2048 each). Code2Wav is a lightweight causal ConvNet (~200M params) enabling single-frame immediate waveform synthesis — no buffering needed.

### 6.3. Qwen3-Omni — VRAM Reality

| Config | VRAM | RTX 5070 Ti (16GB) | RTX 3080 (12GB) |
|---|---|---|---|
| BF16 full | ~79 GB | No | No |
| FP8 | ~35 GB | No | No |
| AWQ 8-bit | ~25 GB | No | No |
| AWQ 4-bit | ~20-25 GB (with encoders + KV cache) | **No** | No |
| AWQ 4-bit + `disable_talker()` | ~15-18 GB | Marginal (text only) | No |

**Verdict**: Qwen3-Omni does not run speech-to-speech on our hardware. Even AWQ 4-bit with encoders + KV cache exceeds 16GB.

### 6.4. Qwen2.5-Omni-7B — The Practical Choice

| | Qwen3-Omni | Qwen2.5-Omni-7B | Qwen2.5-Omni-3B |
|---|---|---|---|
| Architecture | MoE 30B/3.3B active | Dense 7B | Dense 3B |
| VRAM BF16 | ~79 GB | ~31 GB | ~14 GB |
| **VRAM INT4** | **~20-25 GB** | **~7-8 GB** | **~4-5 GB** |
| Tool calling | Yes | Yes | Yes |
| Audio out | 10 languages | Yes | Yes |
| Latency | 234ms | 257ms | Faster |
| **RTX 5070 Ti** | No | **Yes** | **Yes** |
| **RTX 3080** | No | **Yes (INT4)** | **Yes** |

### 6.5. Modalities

**Input**: Text (119 languages), Audio/Speech (19 languages), Images, Video, Audio+Video simultaneous

**Output**: Text (119 languages), Speech (10 languages: EN, ZH, FR, DE, RU, IT, ES, PT, JA, KO)

**French is supported** for both input and output.

3 built-in voices: **Ethan** (male, bright), **Chelsie** (female, warm), **Aiden** (male, laid-back).

### 6.6. Tool Calling in Voice Mode

The Thinker operates in text space, so function calling follows the standard OpenAI-compatible format:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8901/v1", api_key="EMPTY")

tools = [{
    "type": "function",
    "function": {
        "name": "get_current_weather",
        "description": "Get weather for a city",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City name"}
            },
            "required": ["location"],
        },
    },
}]

# Step 1: Audio input, text output for tool detection
response = client.chat.completions.create(
    model="Qwen/Qwen2.5-Omni-7B",
    messages=[{"role": "user", "content": [
        {"type": "audio", "audio_url": "data:audio/wav;base64,..."}
    ]}],
    tools=tools,
    modalities=["text"],  # No audio during tool phase
)

# Step 2: Execute tool, append result to messages

# Step 3: Final response with audio
response = client.chat.completions.create(
    model="Qwen/Qwen2.5-Omni-7B",
    messages=messages,  # includes tool result
    modalities=["audio"],  # Now generate speech
)
```

### 6.7. Local Deployment

#### Transformers (simplest)
```bash
pip install transformers==4.57.3 accelerate qwen-omni-utils flash-attn
```

```python
from transformers import Qwen2_5OmniForConditionalGeneration, Qwen2_5OmniProcessor
from qwen_omni_utils import process_mm_info
import soundfile as sf

model = Qwen2_5OmniForConditionalGeneration.from_pretrained(
    "Qwen/Qwen2.5-Omni-7B",
    torch_dtype="auto", device_map="auto",
    attn_implementation="flash_attention_2",
)
processor = Qwen2_5OmniProcessor.from_pretrained("Qwen/Qwen2.5-Omni-7B")

conversation = [
    {"role": "system", "content": "You are Lyra, a helpful voice assistant."},
    {"role": "user", "content": [
        {"type": "audio", "audio": "/path/to/input.wav"},
    ]},
]

text = processor.apply_chat_template(conversation, add_generation_prompt=True, tokenize=False)
audios, images, videos = process_mm_info(conversation)
inputs = processor(text=text, audio=audios, images=images, videos=videos,
                   return_tensors="pt", padding=True)
inputs = inputs.to(model.device).to(model.dtype)

text_ids, audio = model.generate(**inputs, speaker="Chelsie")
sf.write("response.wav", audio.reshape(-1).detach().cpu().numpy(), samplerate=24000)
```

#### vLLM (production, text output only for now)
```bash
vllm serve Qwen/Qwen2.5-Omni-7B --port 8901 --dtype bfloat16 --max-model-len 32768
```

#### vLLM-Omni (full audio output)
The `vllm-omni` project supports full audio generation. Production-ready support added in v0.16.0.

### 6.8. Quantized Versions (HuggingFace)

**Qwen2.5-Omni-7B**:
- `Qwen/Qwen2.5-Omni-7B-AWQ` — AWQ 4-bit, ~7-8 GB VRAM

**Qwen3-Omni-30B-A3B** (for reference):
- `cyankiwi/Qwen3-Omni-30B-A3B-Instruct-AWQ-4bit` — AWQ 4-bit, ~10 GB weights, ~20-25 GB runtime (67k downloads)
- `TrevorJS/Qwen3-Omni-30B-A3B-GGUF` — GGUF Q4-Q8-F16 (text+vision only in llama.cpp)

### 6.9. Benchmarks (Qwen3-Omni)

| Benchmark | Qwen3-Omni | GPT-4o | Gemini 1.5 Pro |
|---|---|---|---|
| MMLU | 88.7% | 87.2% | 85.6% |
| MMMU (visual) | 82.0% | 79.5% | 76.9% |
| HumanEval (code) | 92.6% | 89.2% | 87.1% |
| AIME 2025 (math) | 65.0% | 53.6% | — |

SOTA on 22/36 audio benchmarks (beating closed-source). Open-source SOTA on 32/36.

### 6.10. Known Limitations

- **Qwen3-Omni**: Massive VRAM — minimum 24GB even quantized for speech-to-speech
- **Batch inference**: Does NOT support audio output (text-only in batch)
- **vLLM standard**: Audio output not supported — requires vLLM-Omni fork
- **Ollama / llama.cpp**: Not supported for Omni variants (audio encoders + Talker not handled)
- **SGLang**: Open feature request, no implementation
- **Long video**: Weak understanding due to limited positional extrapolation

---

## 7. Latency Analysis: Pipeline vs End-to-End

### Pipeline Approach (Current VoiceCLI Pattern)

```
Audio → STT/Whisper (~300-500ms) → LLM + tools (~500-2000ms) → TTS (~200-500ms)
                                                                 = 1-3 seconds minimum
```

Each stage waits for the previous one. With a tool call in the middle, the LLM pass doubles. **Incompatible with natural conversation** (human brain expects ~200-300ms response time).

### Qwen Omni End-to-End

```
Audio → [Thinker reasons + Talker generates audio in parallel streaming] → Audio
                              = ~257ms first packet
```

The Talker starts generating audio **while the Thinker is still reasoning**. First syllable at 257ms — within the natural conversation window.

### With Tool Calling

Tool call latency is **incompressible** regardless of model. But vs pipeline:
- **No STT step** (audio enters the model directly)
- **No separate TTS** (Talker streams audio immediately)
- Saves **~500-1000ms per exchange** compared to a pipeline

### Latency Mitigation Strategies

1. **Vocal filler** — Talker starts speaking ("Let me check..." / "Un instant...") while tool executes
2. **Pre-fetch** — Launch probable tools in parallel with reasoning
3. **Cache** — Frequent queries (weather, time) served without tool call
4. **Streaming** — Code2Wav enables single-frame waveform synthesis, no buffering

---

## 8. VRAM Cohabitation Analysis

### Current State

All daemons managed by the single supervisord at `~/projects/lyra-stack/`.

**ROXABITOWER (local, RTX 5070 Ti 16 GB, on-demand)**:

| Daemon | Model | VRAM | Status | Managed by |
|---|---|---|---|---|
| `voicecli_tts` | Qwen3-TTS 0.6B CustomVoice + 0.6B Base (clone) | **~2.6 GB** | Preloaded, persistent | `make tts` |
| `voicecli_stt` | Faster-Whisper (tiny/base) | **~0.5-1.5 GB** | Lazy-load on first call | `make stt` |
| Kyutai STT (live) | kyutai/stt-1b-en_fr | ~0.5 GB | On-demand (`voicecli listen`) | manual |
| Chatterbox (multilingual) | ResembleAI/chatterbox 350-500M | ~1.8 GB | On-demand (standalone) | manual |

VRAM budget: ~3 GB occupied permanently, ~13 GB free.

**roxabituwer (prod, RTX 3080 12 GB, 24/7)**:

No local inference. Lyra adapters (Telegram, Discord) route through provider APIs (Claude, etc.).

### Scenarios Evaluated

#### Scenario A: Qwen2.5-Omni Replaces Everything (Rejected)

Replace VoiceCLI TTS/STT with a single Omni model.

**Rejected**: VoiceCLI serves a different purpose (production audio for videos/scripts — CUDA graphs, emotion control, voice cloning, Ono_Anna voice). Omni's conversational TTS can't replace that. STT (Whisper, Kyutai) is used daily for dictation. These are everyday tools, not just Lyra components.

#### Scenario B: Same Machine, Mutual Exclusion (Viable, not ideal)

Mode switching on local machine: `make voice-mode` stops TTS/STT, starts Omni. `make tts-mode` does the reverse.

**Issue**: Requires stopping daily-use tools (dictation, TTS) whenever Lyra needs voice. Friction too high for everyday use.

#### Scenario C: Split Machines, No Fallback (Rejected)

Omni on prod, VoiceCLI stays on local.

**Rejected without fallback**: When local machine is off, prod has no TTS/STT capability and no voice-to-voice. Single point of failure.

#### Scenario D: Hybrid Split + Fallback (Selected)

Split workloads across machines with graceful degradation when local is unavailable.

---

### Selected Architecture: Scenario D — Hybrid Split + Mode Switching

#### Design Principle

The two machines have **mutually exclusive GPU workloads** that can't coexist due to VRAM constraints (RTX 3080 = 12 GB). Instead of degrading quality, prod **switches mode** based on local machine availability:

- **Local ON → Omni mode**: Prod runs Qwen2.5-Omni for voice-to-voice. Local provides TTS/STT to both machines.
- **Local OFF → Pipeline mode**: Prod stops Omni, loads TTS/STT locally. Voice-to-voice lost, but TTS/STT quality preserved.

The key constraint: **Omni (~8 GB) and TTS+STT (~3 GB) cannot coexist on the RTX 3080** with enough headroom. So when local goes down, prod must choose — and we choose quality TTS/STT over degraded voice-to-voice.

#### Topology — Local ON (Omni Mode)

```
┌─────────────────────────────────────────────────────┐
│  ROXABITOWER (local, RTX 5070 Ti 16 GB, on-demand)  │
│                                                     │
│  voicecli_tts  (~2.6 GB)  ← always-on              │
│  voicecli_stt  (~0.5 GB)  ← always-on              │
│  Kyutai/Chatterbox         ← on-demand              │
│                                                     │
│  Exposes: HTTP API on LAN (TTS + STT endpoints)     │
│  Used by: Mickael (dictation, video gen)             │
│           + roxabituwer (remote TTS/STT calls)       │
│                                                     │
│  VRAM: ~3 GB / 16 GB = 13 GB free for dev/AI        │
└──────────────────────┬──────────────────────────────┘
                       │ LAN (192.168.1.x)
                       │ HTTP API calls (TTS/STT)
┌──────────────────────▼──────────────────────────────┐
│  roxabituwer (prod, RTX 3080 12 GB, 24/7)           │
│                                                     │
│  lyra_omni     (~8 GB)  ← Qwen2.5-Omni-7B INT4     │
│  lyra_telegram           ← adapter (no VRAM)        │
│  lyra_discord            ← adapter (no VRAM)        │
│                                                     │
│  Voice mode:  Omni handles voice-to-voice natively   │
│  HQ TTS/STT:  calls ROXABITOWER API                  │
│  voicecli_tts: STOPPED (not needed, Omni has TTS)    │
│  voicecli_stt: STOPPED (not needed, Omni has STT)    │
│                                                     │
│  VRAM: ~8 GB / 12 GB = 4 GB headroom for KV cache   │
└─────────────────────────────────────────────────────┘
```

#### Topology — Local OFF (Pipeline Mode)

```
┌─────────────────────────────────────────────────────┐
│  ROXABITOWER — OFF                                  │
│  (no dictation, no video TTS — machine unavailable) │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  roxabituwer (prod, RTX 3080 12 GB, 24/7)           │
│                                                     │
│  lyra_omni:    STOPPED (VRAM freed)                  │
│  voicecli_tts  (~2.6 GB)  ← loaded locally           │
│  voicecli_stt  (~0.5 GB)  ← loaded locally           │
│  lyra_telegram              ← adapter (no VRAM)      │
│  lyra_discord               ← adapter (no VRAM)      │
│                                                     │
│  Pipeline mode:                                      │
│    Audio → Whisper (STT) → Claude API → Qwen3-TTS    │
│    Latency: ~1-3s (no voice-to-voice)                │
│    Tool calling: via Claude API (text-based)          │
│    TTS/STT quality: SAME as local (same models)       │
│                                                     │
│  VRAM: ~3 GB / 12 GB = 9 GB free                     │
└─────────────────────────────────────────────────────┘
```

#### VRAM Allocation Per Mode

| Mode | Machine | Active Models | VRAM Used | VRAM Free |
|---|---|---|---|---|
| **Omni mode** | ROXABITOWER | Qwen3-TTS 0.6B x2 + Whisper | ~3 GB | ~13 GB |
| **Omni mode** | roxabituwer | Qwen2.5-Omni-7B INT4 | ~8 GB | ~4 GB |
| **Pipeline mode** | ROXABITOWER | OFF | — | — |
| **Pipeline mode** | roxabituwer | Qwen3-TTS 0.6B x2 + Whisper | ~3 GB | ~9 GB |

No VRAM contention in either mode. Mode switch on prod is a clean swap: unload one, load the other.

#### Operating Modes in Detail

**Omni mode (local ON) — best experience**:

```
Voice conversation (real-time, speech-to-speech):
  User speaks → Lyra adapter → Qwen2.5-Omni (prod) → Audio response
  Latency: ~257ms first packet
  Tool calling: native via Thinker

High-quality TTS (when needed by prod):
  Lyra needs studio audio → HTTP call to ROXABITOWER → Qwen3-TTS
  Use case: pre-produced voice messages, video narration, voice cloning

File transcription (when needed by prod):
  Lyra receives audio file → HTTP call to ROXABITOWER → Whisper
  Use case: accurate file-based transcription

Local usage (Mickael's daily tools):
  voicecli listen → Kyutai STT → dictation (local only)
  voicecli generate script.md → Qwen3-TTS → WAV (local only)
```

**Pipeline mode (local OFF) — quality preserved, latency sacrificed**:

```
Voice interaction (pipeline, NOT speech-to-speech):
  User speaks → Whisper STT (prod) → text
              → Claude API (LLM + tools) → text response
              → Qwen3-TTS (prod) → Audio response
  Latency: ~1-3s (pipeline overhead)
  Tool calling: via Claude API (standard text-based)
  TTS quality: ✓ SAME as local (same Qwen3-TTS 0.6B models)
  STT quality: ✓ SAME as local (same Whisper model)

File transcription:
  Lyra receives audio file → Whisper (prod) → text
  ✓ SAME quality — identical model

Local usage:
  ✗ UNAVAILABLE — machine is off
```

#### Mode Switching Logic

Prod runs a watchdog that monitors local machine availability and triggers mode switches:

```python
# Pseudo-code for Lyra's mode manager on prod (roxabituwer)
import subprocess

ROXABITOWER_API = "http://192.168.1.XX:8800"  # VoiceCLI HTTP API
HEALTH_CHECK_INTERVAL = 30  # seconds
GRACE_PERIOD = 60  # seconds before switching to pipeline mode

class VoiceMode(Enum):
    OMNI = "omni"          # Qwen2.5-Omni loaded, voice-to-voice active
    PIPELINE = "pipeline"  # TTS/STT loaded locally, cascade mode

current_mode: VoiceMode = VoiceMode.PIPELINE  # safe default on boot

async def check_local_available() -> bool:
    """Ping ROXABITOWER VoiceCLI API health endpoint."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{ROXABITOWER_API}/health")
            return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False

async def switch_to_omni():
    """Local came online → stop TTS/STT, start Omni."""
    global current_mode
    if current_mode == VoiceMode.OMNI:
        return
    log.info("ROXABITOWER available — switching to Omni mode")
    subprocess.run(["supervisorctl", "stop", "voicecli_tts", "voicecli_stt"])
    subprocess.run(["supervisorctl", "start", "lyra_omni"])
    # Wait for Omni to be ready (model load)
    await wait_for_omni_ready(timeout=120)
    current_mode = VoiceMode.OMNI
    log.info("Omni mode active — voice-to-voice enabled")

async def switch_to_pipeline():
    """Local went offline → stop Omni, start TTS/STT."""
    global current_mode
    if current_mode == VoiceMode.PIPELINE:
        return
    log.info("ROXABITOWER unreachable — switching to Pipeline mode")
    subprocess.run(["supervisorctl", "stop", "lyra_omni"])
    subprocess.run(["supervisorctl", "start", "voicecli_tts", "voicecli_stt"])
    # Wait for TTS model to preload
    await wait_for_tts_ready(timeout=60)
    current_mode = VoiceMode.PIPELINE
    log.info("Pipeline mode active — TTS/STT quality preserved, S2S disabled")

async def watchdog():
    """Periodic health check — triggers mode switching."""
    consecutive_failures = 0
    while True:
        if await check_local_available():
            consecutive_failures = 0
            await switch_to_omni()
        else:
            consecutive_failures += 1
            if consecutive_failures * HEALTH_CHECK_INTERVAL >= GRACE_PERIOD:
                await switch_to_pipeline()
        await asyncio.sleep(HEALTH_CHECK_INTERVAL)

# --- Voice routing based on current mode ---

async def handle_voice_message(audio: bytes) -> bytes:
    """Route voice based on current mode."""
    if current_mode == VoiceMode.OMNI:
        # Speech-to-speech via Omni (~257ms)
        return await call_omni_s2s(audio)
    else:
        # Pipeline: STT → LLM → TTS (~1-3s)
        text = await call_local_stt(audio)       # Whisper on prod
        response = await call_llm(text)           # Claude API with tools
        audio_out = await call_local_tts(response) # Qwen3-TTS on prod
        return audio_out

async def transcribe(audio_path: str) -> str:
    """STT — always high quality regardless of mode."""
    if current_mode == VoiceMode.OMNI:
        # Whisper on ROXABITOWER (remote)
        return await call_remote_stt(ROXABITOWER_API, audio_path)
    else:
        # Whisper on prod (local)
        return await call_local_stt(audio_path)

async def synthesize(text: str, voice: str = "Chelsie") -> bytes:
    """TTS — always high quality regardless of mode."""
    if current_mode == VoiceMode.OMNI:
        # Qwen3-TTS on ROXABITOWER (remote)
        return await call_remote_tts(ROXABITOWER_API, text, voice)
    else:
        # Qwen3-TTS on prod (local)
        return await call_local_tts(text, voice)
```

#### What Needs to Be Built

| Component | Where | What | Effort |
|---|---|---|---|
| **VoiceCLI HTTP API** | ROXABITOWER | Expose TTS/STT daemons over HTTP (currently AF_UNIX only) | Medium — wrap existing socket protocol in a FastAPI/uvicorn server, bind to LAN |
| **VoiceCLI on prod** | roxabituwer | Install VoiceCLI + Qwen3-TTS 0.6B + Whisper models | Small — same setup as local, just `uv sync` + model download |
| **lyra_omni supervisor program** | roxabituwer | Serve Qwen2.5-Omni-7B via vLLM-Omni | Medium — new supervisor conf + run script |
| **Mode manager / watchdog** | roxabituwer | Health check + mode switching (Omni ↔ Pipeline) | Medium — see pseudo-code above |
| **Lyra voice router** | roxabituwer | Route voice messages based on current mode | Medium — extend existing `InboundAudio` handling |
| **Firewall / network** | Both | Allow HTTP traffic between machines on VoiceCLI port | Small — ufw rule |

#### Supervisord Configuration

**ROXABITOWER** (existing, unchanged):
```ini
# Already running via ~/projects/lyra-stack/conf.d/
[program:voicecli_tts]    # Qwen3-TTS 0.6B, AF_UNIX socket + new HTTP wrapper
[program:voicecli_stt]    # Faster-Whisper, lazy-load
```

New: HTTP API wrapper process (or extend existing daemons to also listen on TCP).

**roxabituwer** (new — all programs present, mode manager controls which are running):
```ini
[program:lyra_omni]
command=%(ENV_HOME)s/projects/lyra/scripts/run_omni.sh
directory=%(ENV_HOME)s/projects/lyra
autostart=false           # mode manager decides
autorestart=unexpected
# Serves Qwen2.5-Omni-7B INT4 via vLLM-Omni on port 8901

[program:voicecli_tts]
command=%(ENV_HOME)s/projects/voiceCLI/scripts/run_tts.sh
directory=%(ENV_HOME)s/projects/voiceCLI
autostart=true            # safe default: pipeline mode on boot
autorestart=unexpected

[program:voicecli_stt]
command=%(ENV_HOME)s/projects/voiceCLI/scripts/run_stt.sh
directory=%(ENV_HOME)s/projects/voiceCLI
autostart=true            # safe default: pipeline mode on boot
autorestart=unexpected
```

**Boot sequence**: Prod starts in **pipeline mode** (safe default — TTS/STT loaded). Watchdog detects local machine, switches to Omni mode when available.

#### Makefile Targets

```bash
# On roxabituwer (prod) — explicit mode control
make omni-mode      # force switch: stop TTS/STT, start Omni
make pipeline-mode  # force switch: stop Omni, start TTS/STT
make voice-status   # show current mode + health of both machines

# Individual program management
make omni           # lyra_omni status/start/reload/stop/logs/errors
make tts            # voicecli_tts management
make stt            # voicecli_stt management

# On ROXABITOWER (local) — unchanged
make tts            # voicecli_tts management
make stt            # voicecli_stt management
```

### Strength of Each Model in Its Role

| Capability | Qwen2.5-Omni (Omni mode) | VoiceCLI/Qwen3-TTS (Pipeline mode) |
|---|---|---|
| **Primary use** | Real-time voice conversation | TTS/STT with pipeline LLM |
| **Latency** | ~257ms first packet | ~1-3s (pipeline overhead) |
| **Tool calling** | Native (Thinker text space) | Via Claude API (text-based) |
| **Voice quality** | Good (3 built-in voices) | Excellent (CUDA graphs, Ono_Anna, cloning) |
| **Emotion control** | Basic | Advanced (tags, exaggeration, personality) |
| **STT quality** | Good (AuT encoder) | Excellent (Whisper) |
| **When active** | Local machine ON | Local machine OFF (or forced) |

### Degradation Matrix

| Capability | Omni Mode (local ON) | Pipeline Mode (local OFF) |
|---|---|---|
| Voice conversation | **S2S via Omni (~257ms)** | Pipeline: Whisper → Claude → Qwen3-TTS (~1-3s) |
| Tool calling | Native (Omni Thinker) | Via Claude API (same capabilities, higher latency) |
| TTS quality | Qwen3-TTS via remote API (best) | Qwen3-TTS local on prod (best) |
| STT quality | Whisper via remote API (best) | Whisper local on prod (best) |
| Real-time dictation | Kyutai on local | Unavailable (local machine off) |
| Video generation TTS | Qwen3-TTS on local | Unavailable (local machine off) |

**Key guarantees**:
- **TTS/STT quality never degrades** — same Qwen3-TTS 0.6B + Whisper models regardless of mode. Either remote (from local) or loaded directly on prod.
- **Tool calling always available** — via Omni Thinker (fast) or Claude API (standard).
- **Only voice-to-voice is lost** when local is off — replaced by a pipeline with identical voice quality but higher latency (~1-3s vs ~257ms).

---

## 9. Recommendation for Lyra

### Decision Matrix

| Criteria | Weight | Scenario D (Hybrid) | Scenario B (Mutual Excl.) | Pipeline only |
|---|---|---|---|---|
| Runs on our hardware | Critical | **Yes (both machines)** | Yes (one machine) | Yes |
| Tool calling | Critical | **Yes (both modes)** | Yes | Yes |
| Best-case latency | High | **257ms (Omni mode)** | 257ms | 1000-3000ms |
| TTS/STT quality | High | **Never degrades** | Depends on mode | Good |
| Daily tools unaffected | High | **Yes** | No (mode switching) | Yes |
| 24/7 voice+tools | High | **Yes (pipeline fallback)** | No (local is on-demand) | Yes (API-only) |
| Voice-to-voice avail. | Medium | When local ON | When in voice mode | Never |
| Complexity | Low | Medium (watchdog + 2 machines) | Low (1 machine) | Low |

### Implementation Plan

**Phase 1 — Pipeline mode on prod (foundation)**:
1. Install VoiceCLI on roxabituwer (same setup as local: `uv sync` + download Qwen3-TTS 0.6B + Whisper)
2. Add `voicecli_tts` + `voicecli_stt` supervisor programs on prod (autostart=true, safe default)
3. Wire Lyra voice adapter to use local TTS/STT for pipeline mode (Whisper → Claude API → Qwen3-TTS)
4. Validate: voice messages in Telegram get audio responses via pipeline

**Phase 2 — Omni mode on prod**:
1. Add `lyra_omni` supervisor program (autostart=false)
2. Download and serve Qwen2.5-Omni-7B AWQ via vLLM-Omni
3. Validate VRAM fits (~8 GB / 12 GB) with acceptable KV cache headroom
4. Test voice-to-voice flow (audio in → Omni → audio out)
5. Test tool calling flow (audio in → tool call → execute → audio out)
6. Manual mode switching: `make omni-mode` / `make pipeline-mode`

**Phase 3 — Expose VoiceCLI API on local + watchdog**:
1. Add HTTP API wrapper to VoiceCLI TTS/STT daemons on ROXABITOWER (FastAPI on LAN)
2. Firewall rule: allow prod → local on VoiceCLI API port
3. Implement watchdog/mode manager on prod (health check → auto switch Omni ↔ Pipeline)
4. Boot sequence: prod starts in pipeline mode, watchdog detects local → switches to Omni
5. Test full cycle: local ON → Omni mode → local OFF → pipeline mode → local ON → Omni mode

**Phase 4 — GPU upgrade path (24GB+)**:
- Migrate Omni to Qwen3-Omni (same API, drop-in)
- Better benchmarks, MoE efficiency, more languages
- With 24GB: could run Omni + VoiceCLI simultaneously on prod (no mode switching needed)

### Voice Agent Orchestration Frameworks (If Needed)

| Framework | Transport | License | Best For |
|---|---|---|---|
| **LiveKit Agents** | WebRTC | Apache 2.0 | Lowest-latency transport, telephony |
| **Pipecat** (Daily) | WebRTC | BSD 2-Clause | 40+ AI model plugins, <500ms latency |
| **Vocode** | Telephony/Zoom | MIT | Phone calls, Zoom meetings |
| **TEN Framework** | WebRTC + VAD | Apache 2.0 | Built-in VAD, full-duplex |

---

## References

- [Qwen3-Omni Technical Report (arXiv)](https://arxiv.org/html/2509.17765v1)
- [Qwen3-Omni GitHub](https://github.com/QwenLM/Qwen3-Omni)
- [Qwen2.5-Omni GitHub](https://github.com/QwenLM/Qwen2.5-Omni)
- [Qwen2.5-Omni-7B-AWQ (HuggingFace)](https://huggingface.co/Qwen/Qwen2.5-Omni-7B-AWQ)
- [Qwen Function Calling Docs](https://qwen.readthedocs.io/en/latest/framework/function_call.html)
- [vLLM-Omni Docs](https://docs.vllm.ai/projects/vllm-omni/)
- [Voice-to-Voice Models 2026 Review](https://ai.ksopyla.com/posts/voice-to-voice-models-2026-review/)
- [NVIDIA PersonaPlex](https://github.com/NVIDIA/personaplex)
- [Moshi (Kyutai)](https://github.com/kyutai-labs/moshi)
- [Hertz-Dev](https://github.com/Standard-Intelligence/hertz-dev)
- [GLM-4-Voice](https://github.com/THUDM/GLM-4-Voice)
- [Sesame CSM](https://github.com/SesameAILabs/csm)
- [Orpheus TTS](https://github.com/canopyai/Orpheus-TTS)
- [LiveKit Agents](https://github.com/livekit/agents)
- [Pipecat](https://github.com/pipecat-ai/pipecat)
