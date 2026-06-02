# CLAUDE.md — refine-agent plugin

## Purpose

Conversational CLI plugin for refining a Lyra agent profile (persona, voice,
passthroughs, model). Wraps `factory agent refine` — do NOT touch lyra source code,
`~/.roxabi/factory/auth.db` directly, or any file outside this plugin directory.

## Skill

`plugins/refine-agent/skills/refine-agent/SKILL.md`

Trigger: `/refine-agent [agent-name]`

## Storage contract

Agents live in `~/.roxabi/factory/config.db` (SQLite). TOML files are seed-only:

| Source | Role |
|--------|------|
| `src/factory/agents/<name>.toml` | bundled system defaults |
| `~/.roxabi/factory/agents/<name>.toml` | user-level override (machine-specific, gitignored) |

Reads use `factory agent show`. Writes use `factory agent patch` → DB only.
No TOML file is written by this plugin.

→ `docs/agent-management.md` — full CLI reference + DB schema

## refine vs edit vs patch

| Command | Mode | Use when |
|---------|------|----------|
| `factory agent refine` | conversational (this plugin) | exploring or uncertain about target value |
| `factory agent edit` | interactive field editor | direct field mutation, value known |
| `factory agent patch --json` | non-interactive JSON merge | scripted or single-field update |

## Refinable fields

| Aspect | DB field(s) |
|--------|-------------|
| Persona traits / tone | `persona_name`, `persona_json` |
| System prompt | `system_prompt` |
| TTS voice / engine | `voice_json.tts.*` |
| STT engine | `voice_json.stt.*` |
| LLM model | `model` |
| Extended thinking | `effort` |
| Passthrough commands | `passthrough_commands` |

## Round-trip

```
/refine-agent <name>
  → lyra agent show <name>        # read current DB state
  → conversation loop             # propose before/after per field
  → lyra agent patch <name> ...   # write confirmed changes to DB
  → lyra adapter restart          # operator step; ¬done by plugin
```

Override TOML (`~/.roxabi/factory/agents/<name>.toml`) takes precedence on next
`factory agent init --force` — patch DB directly to avoid init reverting changes.

## Boundaries

¬edit `src/factory/` | ¬write TOML files | ¬call DB directly | ¬restart adapters
