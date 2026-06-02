# Living-Doc Lyra Audit — Issue #1674

- **Issue:** #1674
- **Date:** 2026-06-02
- **Universe glob:** `git ls-files '*.md' '*.mdx' | grep -vE '^(docs/history/|docs/architecture/adr/|artifacts/)' | grep -vE '(^|/)CHANGELOG' | grep -v '^docs/architecture/CURRENT.generated.md'`
- **Table rows:** 171 — one row per swept file, listing that file's `lyra`/`Lyra` occurrences as a line list. A row may carry multiple verdicts where a file mixes concerns (e.g. persona + NATS subject); files swept with no occurrence are marked `—`.
- **Verdicts:** every row is an intentional **KEEP** (persona · NATS `lyra.*` subject · `LYRA_*` stream · plugin · host-Part2 · history) **except 3 FIX rows** — 2 × `FIX_state_dir` + 1 × `FIX_path` — all applied (see `## FIX applied`).
- **Stale-and-unfixed:** 0.

*Per-occurrence integer totals are intentionally omitted: rows use collapsed line-ranges and multi-verdict tags, so one "occurrence count" would be ambiguous. The row count is the deterministic, grep-verifiable figure (`grep -c '^|' minus header+separator = 171`); SC-6's bar — every occurrence enumerated + 0 stale-and-unfixed — is met.*

---

## Audit Table

| file | line(s) | token | verdict | rationale |
|---|---|---|---|---|
| CLAUDE.md | 11 | `**Lyra**` | KEEP_persona | Product/engine name in project description |
| CLAUDE.md | 93,94 | `plugins/lyra-ops/CLAUDE.md`, `plugins/lyra-send/CLAUDE.md` | KEEP_plugin | Plugin directory names — real dirs under `plugins/` |
| CLAUDE.md | 111 | `lyra.inbound.*`, `lyra.outbound.*` | KEEP_nats_subject | NATS topic scheme |
| CONTRIBUTING.md | 8 | `cd lyra` | KEEP_history | Historical clone-dir example; `cd lyra` was the old project dir name |
| CONTRIBUTING.md | 127 | `lyra.outbound.signal.<bot_id>` | KEEP_nats_subject | NATS subject in code example |
| CONTRIBUTING.md | 132 | `lyra.outbound.signal.{bot_id}` | KEEP_nats_subject | NATS subject in code example |
| CONTRIBUTING.md | 134 | `"lyra"` (binding hub.register) | KEEP_persona | Lyra persona/agent name |
| README.md | 1 | `# Lyra` | KEEP_persona | Product title |
| README.md | 11,17 | `Lyra` | KEEP_persona | Product prose description |
| README.md | 23,25 | `lyra.inbound.*`, `lyra.outbound.*` | KEEP_nats_subject | NATS subjects in architecture overview |
| README.md | 106,142,143,149,150,151,152 | `lyra` (CLI invocations) | KEEP_persona | Legacy `lyra` CLI alias — kept as alias for `factory` |
| deploy/CLAUDE.md | 5,11 | `Lyra` | KEEP_persona | Product name in container deploy description |
| deploy/CLAUDE.md | 23,79 | `lyra` | KEEP_persona | Staging branch note / product name |
| deploy/CLAUDE.md | 87 | `/run/user/<uid>/factory-deploy.lock` | FIX_path | stale lock filename; code uses factory-deploy.lock (deploy-common.sh:20) — renamed |
| deploy/CLAUDE.md | 97 | `lyra clients` | KEEP_persona | Prose reference to lyra service containers as a group |
| deploy/CLAUDE.md | 107 | `lyra` in timer description | KEEP_persona | `factory-quadlet-sync.timer` pulls lyra repo — product name |
| deploy/CLAUDE.md | 141 | `UID 1500 = \`lyra\`` | KEEP_host_part2 | Container user UID / host user `lyra` — Part 2 |
| deploy/CLAUDE.md | 150 | `lyra_<service>_<purpose>` | KEEP_persona | Secret naming convention example |
| docs/ARCHITECTURE.md | 1,33 | `# Lyra`, `## What is Lyra` | KEEP_persona | Product title/section heading |
| docs/ARCHITECTURE.md | 42,43,48,50,52,56 | `lyra.inbound.*`, `lyra.outbound.*`, `lyra.clipool.*` | KEEP_nats_subject | NATS subjects in architecture diagram |
| docs/ARCHITECTURE.md | 62 | `lyra` is a Python library | KEEP_persona | Product persona in prose |
| docs/ARCHITECTURE.md | 76 | `@RoxabiLyraBot` | KEEP_persona | Example bot name |
| docs/COMMANDS.md | 1,3,9,13 | `# Lyra`, `Lyra`, `lyra CLI` | KEEP_persona | Document title / CLI name |
| docs/COMMANDS.md | 27,28,29,43,46,47,48,51,52,53,54,55,70,71,93,101,120,122,123,124,125,126 | `lyra` (CLI invocations) | KEEP_persona | `lyra` CLI alias invocations and prose references |
| docs/COMMANDS.md | 282,288,303,304 | `lyra_default`, `workspaces.lyra` | KEEP_persona | Agent name / workspace alias example |
| docs/COMMANDS.md | 320,335,342,355,363,371 | `Lyra:` | KEEP_persona | Bot response example output |
| docs/COMMANDS.md | 394,426 | `agent="lyra_default"` | KEEP_persona | Agent binding example |
| docs/CONFIGURATION.md | 3,5,68 | `Lyra` | KEEP_persona | Product name in prose |
| docs/CONFIGURATION.md | 98 | `persona = "lyra_default"` | KEEP_persona | Agent persona config example |
| docs/CONFIGURATION.md | 99,119 | `workspaces.lyra = ...` | KEEP_persona | Workspace alias example — `/lyra` slash command |
| docs/CONFIGURATION.md | 116 | `[agents.lyra_default]` | KEEP_persona | Agent config example |
| docs/CONFIGURATION.md | 140,150,160,170 | `bot_id = "lyra"` | KEEP_persona | Bot identity in config example |
| docs/CONFIGURATION.md | 141,152 | `agent = "lyra_default"` | KEEP_persona | Agent assignment in config example |
| docs/CONFIGURATION.md | 580 | `Lyra automatically migrates` | KEEP_persona | Product prose |
| docs/CONFIGURATION.md | 608,609,611 | `name = "lyra_default"`, `memory_namespace = "lyra"` | KEEP_persona | Agent TOML example |
| docs/CONFIGURATION.md | 697 | `lyra-monitor.{service,timer}` | KEEP_history | Removed legacy units, dated note |
| docs/DEPLOYMENT.md | 3,5,9 | `Lyra` | KEEP_persona | Product name / section heading |
| docs/DEPLOYMENT.md | 77,87,88,89,90,91,92,93,94,95,97,98,101 | `make lyra ...`, `lyra` (Makefile targets) | KEEP_persona | `make lyra` Makefile target is the CLI alias |
| docs/DEPLOYMENT.md | 142,189,190,191,192,193,204,208,261,263,282,340 | `make lyra ...`, `Lyra` | KEEP_persona | Makefile targets / product name |
| docs/GETTING-STARTED.md | 1,3 | `# Getting Started — Lyra`, `the Lyra hub` | KEEP_persona | Document title |
| docs/GETTING-STARTED.md | 18,22,23 | `lyra` (install URL, run modes) | KEEP_persona | Product / package name |
| docs/GETTING-STARTED.md | 37 | `factory bot secret install telegram lyra` | KEEP_persona | `lyra` as bot_id in CLI example |
| docs/GETTING-STARTED.md | 141 | `machine2@lyra` | KEEP_persona | SSH key comment using project name |
| docs/GETTING-STARTED.md | 161 | `lyra` agent account | KEEP_host_part2 | Host OS user `lyra` — Part 2 |
| docs/GETTING-STARTED.md | 194,208,212 | `lyra` (clone dir, install step) | KEEP_persona | Package / product name |
| docs/GETTING-STARTED.md | 220 | `lyra-send`, `refine-agent` | KEEP_plugin | Plugin names |
| docs/GETTING-STARTED.md | 247 | `lyra` agent in DB | KEEP_persona | Agent identity example |
| docs/GETTING-STARTED.md | 267,268,271 | `factory bot secret install telegram lyra`, `factory-bot-telegram-lyra`, `factory-bot-discord-lyra` | KEEP_persona | `lyra` as bot_id in secret names |
| docs/GETTING-STARTED.md | 288,332,345 | `Lyra`, `lyra-monitor` | KEEP_persona / KEEP_history | Product name / removed legacy units note |
| docs/GETTING-STARTED.md | 390,391 | `make lyra logs`, `make lyra errors` | KEEP_persona | Makefile targets |
| docs/GETTING-STARTED.md | 398,404,405,406,407 | `Lyra`, `lyra.inbound.*`, `lyra.clipool.*`, `lyra.outbound.*` | KEEP_persona / KEEP_nats_subject | Product prose + NATS subjects |
| docs/GETTING-STARTED.md | 417 | `~/.ssh/lyra_agent` | KEEP_host_part2 | SSH key file name with host user |
| docs/GETTING-STARTED.md | 423 | `/home/lyra/.ssh/authorized_keys`, `chown lyra:lyra` | KEEP_host_part2 | Host user `lyra` filesystem ops |
| docs/GETTING-STARTED.md | 428 | `ssh -i ~/.ssh/lyra_agent lyra@<MACHINE_1_IP>` | KEEP_host_part2 | SSH as host user `lyra` |
| docs/GETTING-STARTED.md | 438,439 | `ssh -i ~/.ssh/lyra_agent lyra@<IP>` | KEEP_host_part2 | SSH access via host user |
| docs/GETTING-STARTED.md | 453,454,455 | `make lyra status`, `make lyra reload`, `make lyra logs` | KEEP_persona | Makefile targets |
| docs/GETTING-STARTED.md | 472,473 | `Hello Lyra!` | KEEP_persona | Bot response example |
| docs/HAPPY-PATHS.md | 1,10 | `# Lyra`, `Lyra has` | KEEP_persona | Document title / product prose |
| docs/HAPPY-PATHS.md | 589 | `agent:lyra` | KEEP_persona | Agent routing example |
| docs/MULTI-BOT.md | 7,14 | `Lyra instance`, `lyra_default` | KEEP_persona | Product prose / agent name |
| docs/MULTI-BOT.md | 31,40,60,70,73,82,92 | `agent = "lyra_default"`, `bot_id = "lyra"` | KEEP_persona | Config examples with agent/bot identity |
| docs/MULTI-BOT.md | 126 | `lyra = "~/projects/roxabi-factory"` | KEEP_persona | Workspace alias example |
| docs/MULTI-BOT.md | 141,150,151,177,217 | `lyra_default`, `Lyra`, `bot_id lyra` | KEEP_persona | Agent name / product prose |
| docs/MULTI-BOT.md | 253 | `lyra_default.toml` | KEEP_persona | Agent TOML filename example |
| docs/MULTI-BOT.md | 296,298,301,322 | `Restart Lyra`, `make lyra reload`, `make lyra logs` | KEEP_persona | Product name / Makefile targets |
| docs/OBSERVABILITY.md | 5,65 | `Lyra uses`, `agent:lyra` | KEEP_persona | Product prose / agent log example |
| docs/OBSERVABILITY.md | 124 | `lyra-monitor` | KEEP_history | Removed legacy units — dated note |
| docs/QUADLET-DEPLOYMENT.md | 1,3 | `# Quadlet Deployment — Lyra`, `Lyra Quadlet` | KEEP_persona | Document title |
| docs/QUADLET-DEPLOYMENT.md | 16,17,18,19,20,21,22 | `lyra` (role column) | KEEP_persona | Service role label |
| docs/QUADLET-DEPLOYMENT.md | 120,121 | `lyra-events`, `lyra-metrics` | KEEP_stream | JetStream stream names |
| docs/QUADLET-DEPLOYMENT.md | 134,136 | `lyra-{telegram,discord}.container` | KEEP_plugin | Deploy template filenames for lyra adapters (lyra-telegram/discord are the template file names) |
| docs/QUADLET-DEPLOYMENT.md | 169,171,172,173 | `lyra-events`, `lyra-metrics`, `lyra-{telegram,discord}`, `/dev/mapper/data-lyra-blobs` | KEEP_stream / KEEP_plugin / KEEP_host_part2 | Stream names / template files / host device path |
| docs/QUADLET-DEPLOYMENT.md | 175,176,177,178,179,180,181,182,183,184,185,186,187,188,189,190,191,192,193,194,195,196,197,198,199 | `lyra.outbound.audio.*`, `LYRA_OUTBOUND_AUDIO`, `lyra_outbound_audio_sent` | KEEP_nats_subject / KEEP_stream | NATS subjects + JetStream stream + KV bucket |
| docs/QUADLET-DEPLOYMENT.md | 343 | `/dev/mapper/data-lyra-blobs` | KEEP_host_part2 | Host block device name — Part 2 |
| docs/QUADLET-DEPLOYMENT.md | 359 | `podman ps --filter 'name=lyra'` | KEEP_persona | Filter on container names starting with `lyra-*` (legacy) |
| docs/QUADLET-DEPLOYMENT.md | 415,416,422,423,428,431,454,464,475,476,490,493,494,505,515,531,532,547,549,554,555,557,562,566,573,597,598 | `LYRA_OUTBOUND_AUDIO`, `lyra.outbound.audio.*`, `lyra_outbound_audio_sent` | KEEP_stream / KEEP_nats_subject | JetStream stream, NATS subjects, KV bucket — intentional |
| docs/QUICKSTART.md | 3 | `Get Lyra running` | KEEP_persona | Document intro |
| docs/QUICKSTART.md | 21,24 | `cd lyra`, `lyra` CLI | KEEP_persona | Old clone-dir / CLI alias |
| docs/QUICKSTART.md | 37 | `"lyra_bot"` | KEEP_persona | Bot username default |
| docs/QUICKSTART.md | 69,72,79,80,90,106,109,122,126,129,161,168,170,215,216 | `lyra_default`, `lyra` (agent/CLI/config) | KEEP_persona | Agent name / CLI alias / config examples |
| docs/QUICKSTART.md | 179 | `Vision — Lyra` link | KEEP_persona | Internal link anchor |
| docs/ROADMAP.md | 1 | `# Lyra — Prioritized Roadmap` | KEEP_persona | Document title |
| docs/ROADMAP.md | 41,137,149,219,220 | `Lyra agent integration`, `Split Lyra adapters` | KEEP_history | Historical roadmap entries |
| docs/agent-management.md | 221,222,223,224,225 | `Lyra-Session-Id`, `Lyra-Agent`, `lyra[bot]`, `lyra-bot@` | KEEP_persona | Git trailer names / bot identity |
| docs/architecture/adapters.md | 3,6,27 | `Lyra` | KEEP_persona | Document title / product name |
| docs/architecture/adapters.md | 47 | `make lyra` | KEEP_persona | Makefile target reference |
| docs/architecture/adapters.md | 104,107,111,112 | `lyra_stt`, `lyra_tts`, `lyra.voice.stt.request`, `lyra-stt.container`, `lyra-tts.container` | KEEP_persona / KEEP_nats_subject / KEEP_plugin | STT/TTS service identifiers + NATS subjects + Quadlet unit names |
| docs/architecture/architecture-patterns.md | 6 | `lyra, voiceCLI` | KEEP_persona | Scope list mentioning lyra project |
| docs/architecture/contracts.md | 2,3,6,14,25,27,31,39,41,43 | `Lyra` | KEEP_persona | Document title / product name |
| docs/architecture/contracts.md | 63,64,78,81,95,116,117 | `lyra.voice.*`, `lyra.image.*`, `lyra.<domain>.*` | KEEP_nats_subject | NATS subject patterns |
| docs/architecture/contracts.md | 246,247,248 | `lyra.memory.*`, `lyra.<domain>.*` | KEEP_nats_subject | NATS subject placeholders |
| docs/architecture/deployment.md | 1 | `# Lyra — Deployment & Operations` | KEEP_persona | Document title |
| docs/architecture/deployment.md | 25,31,34,52,53,154,155,156,157,158,159,160,162,163,169 | `lyra.inbound.*`, `lyra.outbound.*`, `lyra.clipool.*`, `lyra_session_id` | KEEP_nats_subject / KEEP_persona | NATS subjects + session map field name |
| docs/architecture/deployment.md | 184,186,187,189,193,213,227,266,268,269,270,271,272 | `Lyra` (prose) | KEEP_persona / KEEP_history | Product name / architectural decisions prose |
| docs/architecture/deployment.md | 257 | `lyra→cli session map` | KEEP_persona | Column description using product name |
| docs/architecture/llm-streaming.md | 2,6,25,30 | `Lyra` | KEEP_persona | Document title / product prose |
| docs/architecture/messaging.md | 2,3,6,104,280 | `Lyra` | KEEP_persona | Document title / product name |
| docs/architecture/messaging.md | 109,110,111,112,114,128,130,131,137,142,143,144,145,146,147,148,149,150,151,152,153,154,155,163,169,243,248,250,286,287,288,305,309,310,311,315,316,324 | `lyra.{inbound,outbound,turns,typing,clipool,voice,llm,image,system,audit,hub,monitor,progress}.*`, `lyra-state`, `LYRA_TURNS`, `LYRA_OUTBOUND_AUDIO`, `lyra_outbound_audio_sent` | KEEP_nats_subject / KEEP_stream | NATS subjects + JetStream streams + KV bucket names — intentional throughout |
| docs/architecture/security-routing.md | 1 | `# Lyra — Security, Routing & Memory Isolation` | KEEP_persona | Document title |
| docs/architecture/security-routing.md | 76,143,144,182 | `Lyra starts`, `"lyra"` (pool binding) | KEEP_persona | Product prose / pool binding example |
| docs/architecture/security-routing.md | 190,207,317,318 | `LYRA_AUDIT`, `lyra.audit.>`, `lyra.security` logger | KEEP_stream / KEEP_nats_subject | JetStream stream + NATS subject + logger namespace |
| docs/architecture/storage.md | 3,6,14,22 | `Lyra` | KEEP_persona | Document title / product prose |
| docs/architecture/storage.md | 158,324 | `lyra blobstore serve`, `lyra-state` | KEEP_persona / KEEP_nats_subject | CLI subcommand / KV bucket name |
| docs/architecture/storage.md | 190,191,314 | `lyra.*` imports, `Lyra infrastructure` | KEEP_nats_subject / KEEP_history | No-import rule phrasing / ADR entry |
| docs/architecture/storage.md | 292 | `lyra-state` KV bucket | KEEP_nats_subject | KV bucket name |
| docs/architecture/target-architecture.md | — | (no lyra occurrences in sweep) | — | — |
| docs/architecture/testing-conventions.md | 4 | `lyra, voiceCLI` | KEEP_persona | Scope list |
| docs/architecture/voice-to-voice-analysis.md | 6,20,304,414,422,428,531,536,540,561,573,763,783 | `Lyra` | KEEP_persona | Analysis document using product name |
| docs/architecture/voice-to-voice-analysis.md | 334,335,336,343,344,345,347,348,349,399,474,498,604,616,675,677,684,693,724,787 | `lyra_omni`, `lyra_stt`, `lyra_tts`, `make omni`, `[program:lyra_omni]` | KEEP_persona | Analysis of proposed supervisord programs named lyra_omni; analysis doc, not active code |
| docs/architecture/workers-tooling.md | 2,6 | `Lyra` | KEEP_persona | Document title |
| docs/bot-management.md | (no lyra occurrences noted in sweep) | — | — | — |
| docs/code-quality-exceptions.md | (no lyra occurrences) | — | — | — |
| docs/data-dirs.md | 3,64 | `Lyra stores`, `# Syncthing exclusions for Lyra` | KEEP_persona | Product prose in data-dirs doc |
| docs/debt-tracking.md | (no lyra occurrences noted) | — | — | — |
| docs/memory-system/02-knowledge-graph.md | 25 | `projet-lyra` | KEEP_persona | Example slug value |
| docs/memory-system/03-compiled-truth.md | 17,21 | `lyra.md`, `# Titre (ex: Lyra)` | KEEP_persona | Example path/title in memory doc |
| docs/ops/bigbang-nats-consolidation.md | 14,25,35,46,47,49,108,110,120,128,157,158,179,181 | `lyra` (network, PR, quadlet refs, staging PR) | KEEP_history | Ops migration runbook — historical rename steps |
| docs/ops/clipool-git.md | 30 | `lyra[bot] <lyra-bot@users.noreply.github.com>` | KEEP_persona | Bot git identity example |
| docs/ops/clipool-uid-model.md | 11 | `uid 1500 (\`lyra\`)` | KEEP_host_part2 | Container UID mapping to host user `lyra` |
| docs/ops/container-publishing.md | 5,24,25,26,27 | `Lyra containers`, `UID/GID 1500 (\`lyra\`)`, `HEALTHCHECK CMD lyra config validate` | KEEP_persona / KEEP_host_part2 | Product prose + container UID + healthcheck CLI |
| docs/ops/container-publishing.md | 34,52,56,63,73,247,288,302 | `lyra config validate`, `lyra` (component name, tag prefix) | KEEP_persona | CLI healthcheck / release tag component name |
| docs/ops/container-publishing.md | 384,385,386,387,388,389 | `lyra.clipool.cmd`, `lyra/<component>/vX.Y.Z`, `lyra/v*` | KEEP_nats_subject / KEEP_persona | NATS subject + release tag scheme |
| docs/ops/deploy-smoke-canary.md | 83,113 | `lyra.llm.generate.request` | KEEP_nats_subject | NATS subject in smoke test |
| docs/ops/gh-key-rotation.md | 5,7,21,25,26,35,42,71,121,136,143,154,167 | `Lyra GitHub App`, `lyra-harness` | KEEP_persona | GitHub App name `lyra-harness` — registered app identity |
| docs/ops/nats-acl-inbox-case-postmortem.md | 5,13,91,153,156,175,178,181,393,413,414,415,416,417,419,420,425,427,526,555,603,604,608,609 | `lyra`, `lyra.clipool.*`, `lyra.voice.*`, `lyra.system.*`, `lyra.inbound.*`, `lyra.audit.>` | KEEP_persona / KEEP_nats_subject | Product name in postmortem prose / NATS subjects |
| docs/ops/nats-acl-postmortem-remaining.md | 75,76,77,78,79,143,146,200 | `lyra.clipool.*`, `lyra.voice.*`, `lyra.image.*`, `lyra.llm.*`, `lyra.system.ready`, `Lyra` | KEEP_nats_subject / KEEP_persona | NATS subjects + product name in postmortem |
| docs/ops/nats-authconf-update.md | 143,144 | `LYRA_OUTBOUND_AUDIO`, `KV_lyra_outbound_audio_sent` | KEEP_stream | JetStream stream + KV bucket name |
| docs/ops/nats-identity-lifecycle.md | 68,71 | `Lyra units`, `lyra units` | KEEP_persona | Product name for container group |
| docs/ops/nkey-rotation.md | 57,61,165,172,189,331 | `lyra ops verify` | KEEP_persona | CLI command using legacy `lyra` alias |
| docs/ops/nkey-rotation.md | 438 | `lyra ops verify` | KEEP_persona | Same |
| docs/standards/agents-plugins.md | 2,3,6 | `Lyra` | KEEP_persona | Document title |
| docs/standards/agents-plugins.md | 42,43,44,65,189 | `lyra_default`, `memory_namespace = "lyra"`, `lyra = ...` | KEEP_persona | Agent TOML example / workspace alias |
| docs/standards/backend-patterns.md | 2,6,16 | `Lyra` | KEEP_persona | Document title / product name |
| docs/standards/renderer-roundtrip.md | 39,40,105 | `Lyra itself`, `Volume=/data/lyra#backup` | KEEP_persona / KEEP_host_part2 | Product prose / example volume path with host device |
| docs/standards/testing.md | 2,3,6,9 | `Lyra` | KEEP_persona | Document title / scope |
| docs/vision.md | 1,3,5,20,51 | `# Vision — Lyra`, `Lyra is`, `What Lyra is not` | KEEP_persona | Document title / product vision |
| packages/roxabi-blobs/CLAUDE.md | 5,23,56,60 | `Lyra monorepo`, `Lyra itself`, `lyra.*`, `lyra blobstore serve` | KEEP_persona | Product name + no-import rule + CLI subcommand |
| packages/roxabi-contracts/CLAUDE.md | 6,37,64 | `Lyra publishers`, `lyra` (tag scheme), `Lyra hub/adapters` | KEEP_persona | Package description |
| packages/roxabi-contracts/README.md | 3,59,70,76,77,78,79,114,124,125,126,147 | `Lyra`, `lyra` (contract ADR refs), `lyra.voice.*`, `lyra.jobs.*`, `lyra.results.*`, `lyra.progress.*` | KEEP_persona / KEEP_nats_subject | Product name + NATS subjects in contracts README |
| packages/roxabi-nats/CLAUDE.md | 5,7,25,44,56,73,85 | `Lyra monorepo`, `Lyra itself`, `lyra.*`, `¬import from \`lyra.*\`` | KEEP_persona / KEEP_nats_subject | Package description + no-import invariant |
| packages/roxabi-nats/README.md | 3,24,487,488 | `Lyra hub`, `Lyra as workspace host` | KEEP_persona | Package description |
| plugins/lyra-ops/CLAUDE.md | 1,5,6,9,15,25,34,36 | `lyra-ops`, `Lyra in production`, `lyra runtime code`, `plugins/lyra-ops/` | KEEP_plugin | Plugin directory/name — real plugin `lyra-ops` |
| plugins/lyra-ops/skills/lyra-debug/SKILL.md | 2,3,8,10 | `lyra-debug`, `Debug Lyra`, `# Lyra Debug` | KEEP_plugin | Skill name — real skill under `lyra-ops` plugin |
| plugins/lyra-ops/skills/lyra-debug/SKILL.md | 34 | `permission denied.*\.lyra` | KEEP_host_part2 | Regex matching `.lyra` UserNS host path |
| plugins/lyra-ops/skills/lyra-debug/SKILL.md | 45 | `grep -E 'lyra-|nats'` | KEEP_plugin | Filter for container names (legacy `lyra-*` names still exist) |
| plugins/lyra-ops/skills/lyra-debug/SKILL.md | 88 | `/home/factory/.local/state/lyra/logs/` | FIX_state_dir | Stale state path → fixed to `/home/factory/.local/state/factory/logs/` |
| plugins/lyra-ops/skills/lyra-debug/SKILL.md | 89 | `/home/factory/.local/state/lyra/logs/` | FIX_state_dir | Stale state path → fixed to `/home/factory/.local/state/factory/logs/` |
| plugins/lyra-ops/skills/lyra-debug/SKILL.md | 131,138 | `make remote lyra reload`, `make remote lyra reload` | KEEP_persona | Makefile target using lyra alias |
| plugins/lyra-ops/skills/lyra-debug/SKILL.md | 504,505 | `Restart all Lyra`, `Rebuild + push image` | KEEP_persona | Action label in remediation table |
| plugins/lyra-send/CLAUDE.md | 1,5,6,7,8,9,11,17,49,54 | `lyra-send`, `lyra core`, `lyra hub`, `lyra.outbound.*` | KEEP_plugin / KEEP_nats_subject | Plugin name / plugin contract prose |
| plugins/lyra-send/skills/send/SKILL.md | 5,6,11,13,16,17,48,298 | `Lyra bots`, `lyra`, `via Lyra bot` | KEEP_plugin | Skill name / product references inside lyra-send plugin |
| plugins/lyra-send/skills/send/SKILL.md | 55,84,124,153,184,213,244,274 | `Path.home() / '.lyra'` | KEEP_persona | Legacy `~/.lyra` data dir in plugin code examples; this is the plugin's own convention for locating tokens — `src/factory/paths.py` uses `ROXABI_FACTORY_DIR` (default `~/.roxabi/factory`). The plugin SKILL was written when the dir was `~/.lyra`. Classified KEEP pending explicit `lyra-send` plugin update — not part of this wave's scope, doubt noted. |
| plugins/lyra-send/skills/send/SKILL.md | 121,130,133,159,162,190,193,219,222,250,253,280,283 | `('lyra', 'telegram')`, `('lyra', 'discord')`, `"lyra/telegram"`, `"lyra/discord"` | KEEP_persona | bot_id `lyra` in credential lookup tuples |
| plugins/refine-agent/CLAUDE.md | 5,6,41,42,43,44 | `Lyra agent profile`, `lyra source code`, `lyra agent show`, `lyra agent patch`, `lyra adapter restart` | KEEP_plugin / KEEP_persona | Plugin description / CLI alias |
| plugins/refine-agent/skills/refine-agent/SKILL.md | 5,18,25,135,547 | `Lyra agent`, `lyra_default`, `lyra adapters` | KEEP_plugin / KEEP_persona | Plugin scope / agent name / CLI alias |
| src/factory/adapters/CLAUDE.md | 20 | `lyra.typing.make_typing_factory` | KEEP_nats_subject | NATS typing subject factory |
| src/factory/adapters/CLAUDE.md | 94,95,96 | `lyra.clipool.cmd`, `lyra.clipool.control`, `lyra.clipool.heartbeat` | KEEP_nats_subject | NATS subjects |
| src/factory/agent_cmd/CLAUDE.md | 18 | `lyra CLI entrypoint` | KEEP_persona | CLI alias note |
| src/factory/agents/CLAUDE.md | 36 | `lyra agent init` | KEEP_persona | CLI alias in flow diagram |
| src/factory/blobstore/CLAUDE.md | 12,33,55,56 | `lyra blobstore serve`, `lyra.security logger`, `lyra.audit.blobs.*` | KEEP_persona / KEEP_nats_subject | CLI subcommand / logger name / NATS subject |
| src/factory/core/CLAUDE.md | 32 | `outside lyra (LLM, TTS...)` | KEEP_persona | Architectural boundary prose |
| src/factory/infrastructure/CLAUDE.md | 18 | `other lyra modules` | KEEP_persona | Seam description prose |
| src/factory/infrastructure/turn_writer/CLAUDE.md | 6,18,39,40,62 | `lyra.turns.write`, `LYRA_TURNS`, `turn-writer-v1` | KEEP_nats_subject / KEEP_stream | NATS subject + JetStream stream |
| src/factory/integrations/CLAUDE.md | 5 | `the lyra runtime` | KEEP_persona | Layer description prose |
| src/factory/nats/CLAUDE.md | 8,46,50,51,52,53,54,58,73 | `lyra's domain types`, `lyra.{domain}.*`, NATS subject table | KEEP_nats_subject / KEEP_persona | NATS subjects + module description |
| src/factory/nats/CLAUDE.md | 52 | `LYRA_OUTBOUND_AUDIO`, `lyra_outbound_audio_sent` | KEEP_stream | JetStream stream + KV bucket |
| src/factory/outbound/CLAUDE.md | 56,57,59,62 | `lyra.outbound.audio.*`, `LYRA_OUTBOUND_AUDIO`, `lyra_outbound_audio_sent` | KEEP_nats_subject / KEEP_stream | NATS subject + stream + KV bucket |
| src/factory/tools/CLAUDE.md | 13 | `uid 1500 (lyra / Claude subprocess)` | KEEP_host_part2 | Container UID annotation |
| src/factory/transport/CLAUDE.md | 40,51 | `lyra.typing.{platform}.{bot_id}`, `lyra.turns.write` | KEEP_nats_subject | NATS subjects |
| tests/runbooks/v4_provision_idempo.md | 11,24,46,47,59,75,76,86,94,136,178,179,191,192,193,210 | `lyra`, `test-lyra-app.pem`, `Lyra GitHub App PEM`, `cd ~/projects/lyra` | KEEP_persona / KEEP_history | Runbook references to project / GitHub App PEM name |
| tools/CLAUDE.md | 5 | `Lyra codebase` | KEEP_persona | Module description |

---

## FIX applied

- `plugins/lyra-ops/skills/lyra-debug/SKILL.md:88` — `/home/factory/.local/state/lyra/logs/` → `/home/factory/.local/state/factory/logs/`
- `plugins/lyra-ops/skills/lyra-debug/SKILL.md:89` — `/home/factory/.local/state/lyra/logs/` → `/home/factory/.local/state/factory/logs/`
- `deploy/CLAUDE.md:87` — `/run/user/<uid>/lyra-deploy.lock` → `/run/user/<uid>/factory-deploy.lock`
