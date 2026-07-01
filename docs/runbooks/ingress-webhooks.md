# Runbook — factory-ingress webhooks (ADR-096)

Plane ① sensor: receives GitHub / Cloudflare (and future Vercel) webhooks, verifies,
resolves tenant via `ingress.db`, publishes `factory.event.{connector}.{tenant}.{kind}`.

→ ADR: [`docs/architecture/adr/096-ingress-connector-tenant-registry.mdx`](../architecture/adr/096-ingress-connector-tenant-registry.mdx)

## Data stores (do not conflate)

| Store | Path | Holds |
|---|---|---|
| **auth.db** | `~/.roxabi/factory/auth.db` | User grants, pairing (ADR-090) — **not** connector installs |
| **config.db** | `~/.roxabi/factory/config.db` | Agents, bots |
| **ingress.db** | `~/.roxabi/factory/ingress.db` | `connector_installations` — webhook routing (this service) |

## Public URL

GitHub and Vercel require **internet-reachable HTTPS**. Tailnet bind (`TAILSCALE_IPV4:8780`)
alone is insufficient.

1. Expose via Cloudflare Tunnel or Tailscale Funnel to `factory-ingress:8780`.
2. Register provider webhook URL:
   - GitHub App: `https://<public-host>/webhook/github`
   - Cloudflare (per account): `https://<public-host>/webhook/cloudflare/{factory_tenant}`

**Two GitHub URLs:** factory-ingress (Sentinelle plane ①) is **orthogonal** to roxabi-live
`POST /webhook/github` (D1/backlog). Both may be registered on the same GitHub App.

## First-time setup

```bash
# Secrets (host files → Podman secrets)
echo -n "<github-webhook-secret>" > ~/.roxabi/factory/ingress-github-webhook.tok
echo -n "<cf-webhook-auth>" > ~/.roxabi/factory/ingress-cloudflare-webhook.tok
podman secret create factory-ingress-github-webhook ~/.roxabi/factory/ingress-github-webhook.tok
podman secret create factory-ingress-cloudflare-webhook ~/.roxabi/factory/ingress-cloudflare-webhook.tok

# Config
# ingress.toml (connector registry) is auto-provisioned by deploy/install.sh (prod)
# and deploy/setup.py (dev-setup) from deploy/ingress.toml.example (copy-if-absent). Recreate/edit:
cp deploy/ingress.toml.example ~/.roxabi/factory/ingress.toml   # enable/disable connectors
cp deploy/env/ingress.env.example ~/.roxabi/factory/env/ingress.env
# Set INGRESS_GITHUB_INSTALLATION_ID=<your installation id>

systemctl --user enable --now factory-ingress
curl -s "http://${TAILSCALE_IPV4}:8780/health"
```

## Installation registry (manual, pre-dashboard)

Seed GitHub installation (V1 mono-user):

```bash
# After ADR-096 implementation lands:
factory ingress installation seed github <installation_id> --tenant default
```

Verify row in `ingress.db` before expecting NATS events.

## Secret rotation

1. Update secret at provider (GitHub App settings / Cloudflare webhook).
2. Update host token file.
3. `podman secret create --replace factory-ingress-github-webhook <file>`
4. `systemctl --user restart factory-ingress`

## NATS publish failure (`no response from stream`)

Symptom: GitHub deliveries return **202** but logs show
`ingress publish failed subject=factory.event.github...: nats: no response from stream`.

Cause: JetStream stream **`factory-events`** is missing (common on fresh M₁ installs —
`bootstrap_streams.py` was manual-only before hub boot provisioning).

```bash
# Verify streams exist
cd ~/projects/roxabi-factory
NATS_URL=nats://127.0.0.1:4222 \
  uv run python deploy/nats/bootstrap_streams.py

# Or restart hub (provisions factory-events + factory-metrics on boot)
systemctl --user start factory-hub

# Confirm message landed
NATS_URL=nats://127.0.0.1:4222 uv run python -c "
import asyncio, nats
from pathlib import Path
async def main():
    seed = Path.home().joinpath('.roxabi/factory/nkeys/hub.seed').read_text().strip()
    nc = await nats.connect('nats://127.0.0.1:4222', nkeys_seed_str=seed)
    info = await nc.jetstream().stream_info('factory-events')
    print('factory-events messages:', info.state.messages)
    await nc.close()
asyncio.run(main())
"
```

Redeliver a webhook from GitHub **Recent Deliveries** and re-check stream message count.

## Unknown installation triage

Symptom: GitHub delivers webhooks but no `factory.event.github.*` on NATS (and no publish-failed log).

```bash
# Logs
journalctl --user -u factory-ingress -f | grep unknown_installation

# LogQL (Loki)
{container="factory-ingress"} |= "unknown_installation"
```

Fix: seed `(github, <installation_id>, default)` in `ingress.db`, or re-deliver from GitHub
webhook delivery UI.

HTTP response is always `202 {"accepted": true}` — check logs/metrics, not response body.

## Backup

Include `~/.roxabi/factory/ingress.db` in factory data backup alongside `auth.db` / `config.db`.

## Connector families (operator matrix)

| Connector | Family | URL | Secret | Tenant key |
|---|---|---|---|---|
| GitHub | A (centralized app) | `/webhook/github` | One app secret | `installation.id` → registry |
| Cloudflare | B (per-account) | `/webhook/cloudflare/{tenant}` | Per-tenant `cf-webhook-auth` | `account_id` → registry |
| Vercel | A (future) | `/webhook/vercel` | Integration secret | `configurationId` → registry |

**Do not** configure per-tenant GitHub webhook URLs — GitHub Apps allow one URL per app.