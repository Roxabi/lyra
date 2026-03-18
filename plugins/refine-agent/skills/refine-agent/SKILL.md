---
name: refine-agent
argument-hint: '[agent-name]'
description: >
  Conversationally refine a Lyra agent profile — persona, voice, passthroughs, model.
  Trigger phrases: "refine agent", "edit agent profile", "update agent persona",
  "tune agent voice", "adjust agent settings".
allowed-tools: Bash, Read, Glob
---
# Refine Agent

Let:
- α = agent name (from $ARGUMENTS or user input)
- σ = current agent state (from `lyra agent show`)
- Σ = confirmed changes dict { field → new_value }
- N = iteration count in refinement loop

Interactively refine a Lyra agent's profile. Reads current config, proposes targeted
changes, and applies them via `lyra agent patch`.

## Entry

```
/refine-agent               → prompts for agent name
/refine-agent lyra_default  → refine specific agent
```

## Step 1 — Identify Agent

∃ $ARGUMENTS ⇒ α = $ARGUMENTS (use directly, skip list step).
¬∃ $ARGUMENTS ⇒ run:

```bash
lyra agent list
```

Ask user (AskUserQuestion): "Which agent would you like to refine?" with agent names as
**bold** options.

Verify α exists:

```bash
lyra agent show {α}
```

¬∃ α in output ⇒ "Agent '{α}' not found. Run `lyra agent list` to see available agents."
Stop.

## Step 2 — Read Profile

Capture full agent config as σ:

```bash
lyra agent show {α}
```

Also read system TOML if present (for context, not authoritative):

```bash
cat ~/projects/lyra/src/lyra/agents/{α}.toml 2>/dev/null || echo "(no TOML — DB-only agent)"
```

∃ persona_name in σ ⇒ attempt to read persona file:

```bash
ls ~/.roxabi-vault/personas/ 2>/dev/null || echo "(no persona vault)"
cat ~/.roxabi-vault/personas/{persona_name}.toml 2>/dev/null || echo "(persona file not found)"
```

## Step 3 — Present Profile + Start Conversation

Present current profile in plain language (¬raw JSON dump):

```
Agent: {α}
  Model:       {model}
  Backend:     {backend}
  Persona:     {persona_name} — {key traits if persona file found, else "see DB persona_json"}
  Voice:
    TTS:       {voice_json.tts.engine} / voice: {voice_json.tts.voice}
    STT:       {voice_json.stt.engine}
  Passthroughs: {passthrough list or "none"}
  Plugins:     {enabled plugins or "none"}
```

Ask user (AskUserQuestion): "What would you like to change?"
Options: **persona**, **voice (TTS/STT)**, **passthroughs**, **model**, **other field**,
**done**.

## Step 4 — Refinement Loop

N = 0. Repeat while operator has not said done/exit/quit:

1. Listen to operator description of desired change.
2. Map request → specific AgentRow field(s). Common mappings:
   - "voice" / "TTS voice" → `voice_json.tts.voice`
   - "STT engine" → `voice_json.stt.engine`
   - "model" / "LLM" → `model`
   - "persona" → `persona_name` and/or `persona_json`
   - "passthrough" → `passthrough_commands` (list)
   - "system prompt" → `system_prompt`
3. Propose change with before/after values:

   > "I'd suggest changing `voice_json.tts.voice` from `'echo'` to `'nova'` for a warmer
   > tone. Confirm? **[y/N]**"

4. ∃ confirmation (y/yes) ⇒ add to Σ, continue loop.
   ¬∃ confirmation ⇒ discard, ask what else to change.
5. N = N + 1.

¬∃ changes after full loop ⇒ skip Step 5, output "No changes applied."

## Step 5 — Apply Changes

∀ (field, value) in Σ, apply via:

```bash
lyra agent patch {α} --json '{"{field}": {value}}'
```

For nested JSON fields (e.g. `voice_json`), build full replacement object:

```bash
lyra agent patch {α} --json '{"voice_json": {"tts": {"engine": "kokoro", "voice": "nova"}, "stt": {"engine": "whisper"}}}'
```

Show applied diff after all patches:

```
Applied to {α}:
  voice_json → {"tts": {"voice": "nova", ...}}
  model      → claude-haiku-4-5-20251001
```

Remind operator: "Run `lyra agent init --force` or restart lyra adapters for voice/model
changes to take effect."

## Completion

Output:
- Summary of all changes applied (or "No changes applied" if Σ = ∅).
- Next-step reminder if changes were made.

## Edge Cases

- ¬∃ α ⇒ error + list, stop.
- Invalid JSON returned by patch ⇒ show error, retry with corrected value (ask operator
  for clarification).
- Σ = ∅ after loop ⇒ "No changes applied." (¬run patch).
- Persona file not found ⇒ use `persona_json` from DB only (¬block on missing file).
- Operator asks about a field not in AgentRow ⇒ clarify which fields are patchable, show
  field list from `lyra agent show` output.

$ARGUMENTS
