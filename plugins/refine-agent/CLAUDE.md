# CLAUDE.md — refine-agent plugin

## Purpose

Conversational CLI plugin for refining a Lyra agent profile (persona, voice,
passthroughs, model). Wraps `lyra agent refine` — do NOT touch lyra source code,
`~/.lyra/auth.db` directly, or any file outside this plugin directory.

## Skill

`plugins/refine-agent/skills/refine-agent/SKILL.md`

Trigger: `/refine-agent [agent-name]`

## Storage contract

Agents live in `~/.lyra/auth.db` (SQLite). TOML files are seed-only:

| Source | Role |
|--------|------|
| `src/lyra/agents/<name>.toml` | bundled system defaults |
| `~/.lyra/agents/<name>.toml` | user-level override (machine-specific, gitignored) |

Reads use `lyra agent show`. Writes use `lyra agent patch` → DB only.
No TOML file is written by this plugin.

→ `docs/agent-management.md` — full CLI reference + DB schema

## refine vs edit vs patch

| Command | Mode | Use when |
|---------|------|----------|
| `lyra agent refine` | conversational (this plugin) | exploring or uncertain about target value |
| `lyra agent edit` | interactive field editor | direct field mutation, value known |
| `lyra agent patch --json` | non-interactive JSON merge | scripted or single-field update |

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

Override TOML (`~/.lyra/agents/<name>.toml`) takes precedence on next
`lyra agent init --force` — patch DB directly to avoid init reverting changes.

## Boundaries

¬edit `src/lyra/` | ¬write TOML files | ¬call DB directly | ¬restart adapters
