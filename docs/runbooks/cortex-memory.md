# cortex-memory satellite — provision & operate

Knowledge capture/search/assemble for factory (ADR-087). Replaces `roxabi-vault` CLI.

**Default branch:** `roxabi-cortex` **`main` only** (no staging).
**Image:** `ghcr.io/roxabi/cortex-memory:main` (CI publish on push to `main`).
**Runtime:** **Quadlet only** on M₁ — no long-lived user process unit.

## Subjects

| Subject | Direction |
|---|---|
| `roxabi.memory.capture` | hub → cortex (request-reply) |
| `roxabi.memory.query.search` | hub → cortex |
| `roxabi.memory.query.assemble` | hub → cortex (agent system-prompt inject) |
| `roxabi.memory.heartbeat` | cortex → hub |

Identity: **`cortex-memory`** (ACL external) · secret: **`factory-nats-cortex-memory`**.

## Data migration (legacy vault)

One-shot import from `~/.roxabi-vault/vault.db` into `~/.cortex/memory.db`:

```bash
cd ~/projects/roxabi-cortex/packages/memory
uv sync
uv run cortex-memory import-vault \
  --vault-db ~/.roxabi-vault/vault.db \
  --db ~/.cortex/memory.db
uv run cortex-memory stats --db ~/.cortex/memory.db
```

M₁ status (2026-07-22): **829 entries** imported (matches vault row count). Source DB left in place; folder may live under `~/projects/archived/roxabi-vault`.

## One-time provision (M₁)

```bash
# 1. Nkey (no rotate others)
cd ~/projects/roxabi-factory
factory-genkeys --add-identity cortex-memory   # → ~/.roxabi/factory/nkeys/cortex-memory.seed

# 2. Regen NATS auth.conf + restart stack
make nats-regen-authconf

# 3. Podman secret for Quadlet
podman secret create factory-nats-cortex-memory \
  ~/.roxabi/factory/nkeys/cortex-memory.seed

# 4. Import vault data (once)
mkdir -p ~/.cortex
cd ~/projects/roxabi-cortex && git pull --ff-only origin main
cd packages/memory && uv sync
uv run cortex-memory import-vault --db ~/.cortex/memory.db

# 5. Quadlet unit
mkdir -p ~/.config/containers/systemd
ln -sfn ~/projects/roxabi-cortex/packages/memory/deploy/quadlet/cortex-memory.container \
  ~/.config/containers/systemd/cortex-memory.container
systemctl --user daemon-reload
systemctl --user enable --now cortex-memory.service
systemctl --user status cortex-memory.service
```

Image pull (after CI publish):

```bash
podman pull ghcr.io/roxabi/cortex-memory:main
systemctl --user restart cortex-memory.service
```

Local image build (if GHCR unavailable):

```bash
cd ~/projects/roxabi-cortex
podman build -f packages/memory/Dockerfile \
  --build-context factory=../roxabi-factory \
  -t ghcr.io/roxabi/cortex-memory:main packages/memory
# retag for unit if needed: same name as Image= in the .container file
```

## Dev (process, not Quadlet)

```bash
export NATS_URL=nats://127.0.0.1:4222
export NATS_NKEY_SEED_PATH=~/.roxabi/factory/nkeys/cortex-memory.seed
cd ~/projects/roxabi-cortex/packages/memory
uv run cortex-memory serve --db ~/.cortex/memory.db
```

## Smoke

```bash
# hub nkey + contracts from factory checkout
export NATS_URL=nats://127.0.0.1:4222
export NATS_NKEY_SEED_PATH=~/.roxabi/factory/nkeys/hub.seed
cd ~/projects/roxabi-cortex/packages/memory
uv run python - <<'PY'
import asyncio, os
from roxabi_contracts.memory import build_search_request, SearchResponse, SUBJECTS
from roxabi_nats.connect import nats_connect

async def main():
    nc = await nats_connect(os.environ["NATS_URL"], identity_name="hub")
    try:
        req = build_search_request(query="claude", limit=3)
        msg = await nc.request(SUBJECTS.query_search, req.model_dump_json().encode(), timeout=5)
        r = SearchResponse.model_validate_json(msg.data)
        print("ok", r.ok, "hits", len(r.hits))
        for h in r.hits:
            print("-", h.title[:80])
    finally:
        await nc.drain()

asyncio.run(main())
PY
```

Product path: hub `CortexVault` (SessionTools / assemble / `/search`) → NATS → this satellite.
`/vault-add` product path removed 2026-08-01 — capture is cortex-owned, not a hub slash command.

## Fail-open

Agent system-prompt assemble uses a **2s** timeout. If cortex is down, the turn
proceeds with the static system prompt only (ADR-087).
