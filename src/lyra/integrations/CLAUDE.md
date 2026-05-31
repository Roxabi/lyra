# CLAUDE.md — lyra.integrations

## Role

This is THE boundary layer between the lyra runtime and anything external: OS
daemons, CLI tools, and HTTP-backed services. Any code that shells out, spawns a
subprocess, or calls an external HTTP API belongs here — nowhere else.

## base.py — Protocols and shared errors

`base.py` defines the interfaces every integration must satisfy via
`typing.Protocol` (runtime-checkable, mirrors the `lyra.llm.base` pattern):

| Protocol | Responsibility |
|---|---|
| `ScrapeProvider` | `async scrape(url, timeout) → str` |
| `VaultProvider` | `async add(…) → None` / `async search(query) → str` |
| `AudioConverter` | `async convert_wav_to_ogg(wav_path, ogg_path) → None` |
| `SystemctlManager` | `async control(action, service) → str` |

`SessionTools` is the injection bundle handed to plugin commands at registration.

Adding a new integration → implement the matching Protocol (or define a new one
in `base.py`) before writing the concrete class.

## Two categories

### OS control — `systemctl.py`

Affect live host state (start/stop/restart systemd user units). Side-effects are
intentional and irreversible within a call.

- `SystemctlManager` is current (`/svc` plugin).
- Raises `ServiceControlFailed(reason)` on subprocess error or timeout.
- Callers must not validate/sanitize service names a second time — the command
  boundary (plugin command layer) already enforced authorization.

### External services — `vault_cli.py`, `web_intel.py`, `audio.py`

Drive out-of-process tools to fetch or store data.

- `VaultCli` — shells to the `vault` CLI; `search` swallows errors (non-fatal),
  `add` raises `VaultWriteFailed` (write failure is actionable).
- `WebIntelScraper` — spawns the web-intel plugin's scraper subprocess; raises
  `ScrapeFailed` on error.
- `audio.py` — WAV→OGG conversion via subprocess; raises `AudioConversionFailed`.

## Failure model

Integrations raise typed, structured exceptions (`ServiceControlFailed`,
`AudioConversionFailed`, `VaultWriteFailed`, `ScrapeFailed`). Silent degradation
is forbidden — callers must handle or propagate explicitly.

The sole exception to "raise on failure": `VaultProvider.search` swallows errors
because a failed search must never block a conversation turn.

## Trust model

OS-control integrations receive TRUSTED inputs. The plugin command layer
(callers) already validated and authorized the request. Do not re-sanitize
inside an integration — it adds no security and creates mismatches.

External-service integrations still validate CLI arguments structurally (e.g.
`_SAFE_CLI_ARG_RE` in `vault_cli.py`) to prevent subprocess injection, not
authorization — that distinction matters.

## Dependency direction

```
plugin commands (src/lyra/commands/)
processors      (src/lyra/core/processors/)
simple_agent    (src/lyra/agents/simple_agent.py)
bootstrap factory
        ↓
  lyra.integrations   ← you are here
        ↓
  external world (systemd, vault CLI, web-intel, ffmpeg)
```

Integrations must not import from `lyra.commands`, `lyra.agents`, or any layer
above. They may import from `lyra.core.exceptions` for shared error types.
