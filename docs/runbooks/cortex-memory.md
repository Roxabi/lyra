# cortex-memory satellite — provision & operate

Knowledge capture/search/assemble for factory (ADR-087). Replaces `roxabi-vault` CLI.

## Subjects

| Subject | Direction |
|---|---|
| `roxabi.memory.capture` | hub → cortex (request-reply) |
| `roxabi.memory.query.search` | hub → cortex |
| `roxabi.memory.query.assemble` | hub → cortex (agent system-prompt inject) |
| `roxabi.memory.heartbeat` | cortex → hub |

Identity: **`cortex-memory`** · secret: **`factory-nats-cortex-memory`**.

## One-time provision (M₁)

```bash
# 1. ACL already in deploy/nats/acl-matrix.json — regenerate auth.conf
cd ~/projects/roxabi-factory
make nats-regen-authconf   # or: factory-genkeys (regen authconf from seeds)

# 2. Add nkey for the new identity (does not rotate other seeds)
factory-genkeys --add-identity cortex-memory

# 3. Podman secret for the container
podman secret create factory-nats-cortex-memory \
  ~/.roxabi/factory/nkeys/cortex-memory.seed

# 4. Import legacy vault data (optional, once)
cd ~/projects/roxabi-cortex/packages/memory
uv run cortex-memory import-vault   # → ~/.cortex/memory.db

# 5. Install Quadlet unit
mkdir -p ~/.config/containers/systemd
ln -sfn ~/projects/roxabi-cortex/packages/memory/deploy/quadlet/cortex-memory.container \
  ~/.config/containers/systemd/cortex-memory.container
systemctl --user daemon-reload
systemctl --user start cortex-memory.service
systemctl --user status cortex-memory.service
```

## Local dev (no Quadlet)

```bash
export NATS_URL=nats://127.0.0.1:4222   # unauthenticated local NATS, or seed path set
cd ~/projects/roxabi-cortex/packages/memory
uv run cortex-memory import-vault --limit 0
uv run cortex-memory serve
```

Factory hub uses `CortexVault` always — ensure the same `NATS_URL` and that
`cortex-memory` is subscribed before `/vault-add` / first-turn assemble.

## Smoke

```bash
# from host with nats CLI + hub nkey (or open local NATS)
nats req roxabi.memory.query.search \
  "$(python -c 'from roxabi_contracts.memory import build_search_request; print(build_search_request(query=\"claude\").model_dump_json())')"
```

## Fail-open

Agent system-prompt assemble uses a **2s** timeout. If cortex is down, the turn
proceeds with the static system prompt only (ADR-087).
