# OpenClaw-RL — RL Training from Conversations

> Source: https://github.com/Gen-Verse/OpenClaw-RL
> Tier: 2 (Reference)
> Local clone: ~/projects/external_repo/memory/OpenClaw-RL/
> Visual: [openclaw-rl-architecture.html](./openclaw-rl-architecture.html)

## Summary

OpenClaw-RL is a **fully asynchronous reinforcement learning framework** that turns everyday multi-turn conversations into training signals for a self-hosted language model. The key insight: instead of collecting labeled datasets, the system wraps a local model behind an OpenAI-compatible API proxy, intercepts live conversations, and uses the **next user/environment message** as a natural reward signal. A Process Reward Model (PRM) asynchronously scores each turn, and the policy is updated in the background while the model continues serving requests.

Two optimization paradigms are supported:
1. **Binary RL (GRPO)**: PRM assigns scalar +1/-1/0 rewards per turn; GRPO broadcasts the reward uniformly to all response tokens; PPO-style clipped loss trains the policy.
2. **On-Policy Distillation (OPD)**: PRM extracts a textual "hindsight hint" from the next state, constructs an enhanced teacher prompt, and the token-level log-probability gap between teacher and student becomes a directional advantage signal — richer than any scalar reward.

Built on [SLIME](https://github.com/THUDM/slime) (Megatron + SGLang) with Ray for GPU orchestration. Released 2026-02-26.

## Key Components

### File Structure

```
openclaw-rl/                        # Binary RL method
├── openclaw_api_server.py          # FastAPI proxy + PRM scoring + sample submission (~730 lines)
├── openclaw_rollout.py             # Async rollout worker, bridges API server ↔ SLIME trainer
├── run_qwen3_4b_openclaw_rl.sh     # Launch script with all hyperparameters

openclaw-opd/                       # On-Policy Distillation method
├── openclaw_opd_api_server.py      # FastAPI proxy + hint extraction + teacher log-prob query (~900 lines)
├── openclaw_opd_rollout.py         # Rollout bridge for OPD
├── topk_distillation_loss.py       # Custom reverse-KL loss for Top-K distillation variant
├── run_qwen3_4b_openclaw_opd.sh    # Token-level OPD launch script
├── run_qwen3_4b_openclaw_opd_topk.sh # Top-K distillation variant

slime/                              # Base RL framework (THUDM)
├── train_async.py                  # Async training loop (Ray-based)
├── slime/                          # Core: rollout, backends, utils

openclaw/                           # OpenClaw IDE/agent (bundled snapshot)
instructions/                       # Environment setup guide
```

### 4-Component Async Architecture

| Component | Role | GPU Allocation (default 8-GPU) |
|-----------|------|-------------------------------|
| **API Proxy Server** | FastAPI on :30000. Intercepts OpenAI API calls. Classifies main vs side turns. Tokenizes, collects log-probs, triggers PRM. | 0 (CPU) |
| **SGLang Rollout Engine** | Serves the policy model. OpenAI-compatible `/v1/chat/completions`. TP=2 per engine. | 2 GPUs |
| **PRM Judge** | Separate SGLang instance running the judge model. m=3 parallel evaluations. Also computes teacher log-probs for OPD. | 2 GPUs |
| **SLIME Actor (Megatron)** | Training with TP=4, full recompute, CPU optimizer offload. Holds trainable weights. | 4 GPUs |

### Training Pipeline

```
User → OpenClaw Agent → API Proxy (:30000)
                            ↓
                   Forward to SGLang (inference)
                            ↓
                   Response + per-token log-probs
                            ↓
                   Return to user (zero latency impact)
                            ↓ (async, on next turn)
                   PRM scores previous turn using next-state
                            ↓
                   Sample submitted to output_queue
                            ↓
                   SLIME drains batch → train → update weights
                            ↓
                   SGLang reloads weights → model improved
```

### Key API Headers

OpenClaw sends three custom signals per request:
- `X-Session-Id`: Tracks multi-turn conversation identity
- `X-Turn-Type`: `"main"` (trainable) or `"side"` (non-trainable: tool calls, system, etc.)
- `X-Session-Done`: Signals end of session for cleanup

## Memory & Learning Loop

### How Conversations Become Training Signals

1. **Request interception**: Every API call passes through the FastAPI proxy. Side turns are forwarded but produce no training data. Main turns are tokenized and buffered.

2. **Next-state as reward signal**: When a new main turn arrives, the **previous turn's next-state** is the current user/environment message. This is the core insight — no explicit labeling needed. The temporal structure of conversation IS the reward signal.

3. **PRM evaluation** (async, non-blocking):
   - **Binary RL**: Judge prompt asks "did the assistant's output successfully fulfill the user's intent?" with the next state as evidence. m=3 independent votes, majority wins. Score: +1 (good), -1 (bad), 0 (neutral/ambiguous).
   - **OPD**: Judge prompt asks "does the next state reveal useful hindsight?" and extracts a concrete hint wrapped in `[HINT_START]...[HINT_END]`. Selects the longest non-trivial hint among positive votes. No hint = sample dropped.

4. **Sample construction**:
   - Tokens = prompt_ids + response_ids
   - rollout_log_probs = per-token log-probs from policy at serve time
   - loss_mask = [1] * n_response (effective) or [0] * n_response (excluded)
   - reward = {"score": float} for Binary RL
   - teacher_log_probs = [T] tensor for OPD (from enhanced teacher)

5. **Training**: SLIME drains `rollout_batch_size` samples (default 16), computes loss, backprops. Weight sync to SGLang is periodic and brief.

### Policy Evolution

The policy evolves in a **continuous online loop**:
- Phase 1 (Serve & Collect): SGLang serves requests, proxy collects samples, PRM scores async
- Phase 2 (Train Batch): Actor trains on collected batch, serving continues with old weights
- Phase 3 (Weight Sync): Brief pause (~seconds), actor pushes weights to SGLang, submission resumes

Critical safety mechanisms:
- **submission_enabled Event**: Gates sample submission during weight updates (returns HTTP 503)
- **At-least-one guarantee** (Binary RL): Every session contributes at least one effective training sample, even if scored 0
- **Record purging**: JSONL records cleared on each training cycle to prevent stale data
- **Graceful degradation**: PRM failures default to score 0, OPD drops samples without valid hints

## Relevance for Lyra

### Direct Relevance: Procedural Memory (Level 4)

OpenClaw-RL demonstrates a **concrete implementation of learning from interactions** — exactly what Lyra's Level 4 (procedural memory) needs for Phase 3. The key patterns:

1. **Conversation-as-training-data**: Lyra's episodic memory (Level 2) already stores conversation transcripts. OpenClaw-RL shows how to extract gradient signals from these conversations without explicit labeling.

2. **Next-state as implicit feedback**: In Lyra's context, this maps to:
   - User corrects the agent → negative signal
   - User says thanks/moves on → positive signal
   - Tool returns error → negative signal
   - These are already observable in Lyra's session transcripts

3. **PRM as reward model**: Lyra could use a small local model (or even the same model with a different prompt) to evaluate past interactions. The majority-voting pattern (m=3) is robust and cheap.

4. **Async architecture**: Lyra's hub-and-spoke asyncio architecture already separates serving from processing. Adding a background training loop is architecturally compatible.

### What Lyra Could Adopt

| OpenClaw-RL Pattern | Lyra Adaptation | Phase |
|---------------------|----------------|-------|
| Next-state reward signal | Score conversation turns from episodic memory transcripts | P3 |
| PRM majority voting | Small SLM as judge (Qwen 1.5B-3B on Machine 1) | P3 |
| Session tracking with headers | Already exists: pool bindings + session JSONL | P1 (already done) |
| Hint extraction (OPD) | Extract "what should I have done" from user corrections → procedural rules | P3 |
| JSONL logging of all evaluations | Store PRM scores in semantic memory for trend analysis | P3 |
| At-least-one guarantee | Ensure every conversation contributes to learning | P3 |

### What Lyra Should NOT Adopt

- **Full SLIME/Megatron training stack**: Way too heavy for personal use. Lyra's models are fixed (cloud Anthropic + local Ollama). Fine-tuning 4B+ models requires 8 GPUs.
- **Real-time policy gradient updates**: Lyra doesn't train its own weights. Instead, procedural memory should be **soft** — learned preferences, rules, and patterns stored in SQLite, not weight updates.
- **SGLang serving**: Lyra uses Ollama for local models, not SGLang. Different serving stack.

## Actionable Patterns

### 1. Conversation Scoring Pipeline (Phase 3)

Adapt the PRM scoring pattern for Lyra's episodic memory:

```python
# Pseudo-code for Lyra's procedural memory builder
async def score_past_interaction(turn, next_state):
    """Score a past conversation turn using the next message as evidence."""
    prompt = build_judge_prompt(turn.response, next_state.content, next_state.role)
    scores = await asyncio.gather(*[
        query_local_slm(prompt) for _ in range(3)  # m=3 majority vote
    ])
    return majority_vote(scores)  # +1, -1, 0
```

This runs offline on stored episodic transcripts, not live. No model training needed — just score and store.

### 2. Hindsight Hint Extraction (Phase 3)

The OPD hint extraction pattern is directly usable for building procedural rules:

```
Input:  (agent_response, user_correction)
Output: "When user asks for X, always check Y first"
```

These hints become entries in Level 4 procedural memory — retrieved and injected into future prompts as system instructions.

### 3. Implicit Feedback Classification

OpenClaw-RL's turn classification (`main` vs `side`) maps to Lyra's need to distinguish:
- **Learning-relevant turns**: User corrections, explicit feedback, task outcomes
- **Noise**: System messages, tool routing, meta-commands

### 4. Session-Aware Batching

The `session_id` + turn counter pattern ensures temporal ordering is preserved. Lyra's pool-based session tracking already does this. The at-least-one guarantee is a useful heuristic to adopt.

### 5. JSONL Audit Trail

All PRM evaluations logged to JSONL with session_id, turn, score, votes, and representative evaluation text. Essential for debugging and understanding what the system learns. Lyra should log all procedural memory decisions similarly.

## Risks & Limitations

### Compute Requirements
- **Default: 8 GPUs** (4 actor + 2 rollout + 2 PRM). Lyra has 1 RTX 3080 (10GB) + 1 RTX 5070 Ti (16GB). Full OpenClaw-RL is completely infeasible on Lyra's hardware.
- Even the PRM scoring alone (m=3 parallel evaluations of a 4B model) requires significant GPU memory.

### Architectural Mismatch
- OpenClaw-RL **trains model weights**. Lyra uses cloud APIs (Anthropic) and local inference (Ollama) — neither supports online weight updates.
- The feedback loop is real-time (live conversation). Lyra's learning should be **offline batch processing** on stored transcripts.

### Quality Risks
- PRM scores are noisy. Majority voting helps but doesn't eliminate false positives/negatives.
- OPD's hint extraction can produce shallow or misleading hints. The >10 char filter is minimal.
- Without careful curation, learned procedural rules could degrade over time (concept drift).

### What Doesn't Fit
- The entire SGLang/Megatron/Ray stack — too heavy, wrong serving layer
- Real-time gradient computation — Lyra doesn't have trainable parameters
- The "same model as both student and teacher" OPD pattern — Lyra's models are heterogeneous (cloud vs local)

## Priority

**Phase 3** — This is advanced auto-improvement functionality.

- **Phase 1** (current): No direct use. Focus on working memory + semantic search. Session tracking already exists.
- **Phase 2**: Relevant only as conceptual reference for how SLMs could score interactions. The PRM prompt templates could be adapted for Lyra's memory-scoring SLM.
- **Phase 3**: Core relevance. Lyra's procedural memory builder should:
  1. Batch-process episodic transcripts (Level 2)
  2. Score turns using a local SLM as PRM judge (adapted from OpenClaw-RL's prompt)
  3. Extract hindsight hints from negative interactions (adapted from OPD)
  4. Store scored rules in Level 4 procedural memory (SQLite)
  5. Inject relevant procedural rules into future prompts

**Estimated effort for Lyra adaptation**: Small-Medium. The PRM scoring pipeline is ~200 lines of Python. The training loop is irrelevant. The hint extraction prompt is directly reusable. Main work: integrating with Lyra's memory levels and running scoring offline on stored transcripts rather than live.
