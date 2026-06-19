# Runbook — Secrets disaster recovery (lost NATS nkeys)

Use when **`~/.roxabi/factory/nkeys/*.seed` are lost or compromised** and you must wipe and regenerate all NATS identities from `deploy/nats/acl-matrix.json`.

For rotating a single nkey without wiping the directory, see [secrets-rotation.md](secrets-rotation.md). For suspected compromise of one identity, see [nkey-rotation.md](../ops/nkey-rotation.md).

## What this resets

| Touched | Untouched |
|---------|-----------|
| `~/.roxabi/factory/nkeys/*.seed` (backup → `nkeys.bak.{epoch}/` first) | Bot tokens (`factory-telegram-*`, Discord, …) |
| `~/.roxabi/factory/nkeys/auth.conf` | `gh-app.pem`, `claude-oauth.tok`, `litellm-key.tok`, `blobstore.tok` |
| Podman secrets `factory-nats-*` (via `install.sh --secrets-only`) | `config.db`, `auth.db`, `turns.db`, JetStream streams |
| `/etc/nats/nkeys/auth.conf` only if `FACTORY_ACL_WRITE_ETC_NATS=1` | Vaultwarden / future broker store (see below) |

`factory-acl genkeys --regen-authconf` **does not wipe** — it re-renders `auth.conf` from existing seeds. If `.seed` files are gone, you need full regeneration (`--regenerate` or `factory secrets reset`).

## One-command recovery (M₁)

Run from the `roxabi-factory` checkout on the hub host (e.g. `roxabituwer`):

```bash
factory secrets reset --yes --converge
```

Steps executed:

1. Backup then wipe `~/.roxabi/factory/nkeys/`
2. `factory-acl genkeys --regenerate` — provision all active identities + render `auth.conf`
3. `./deploy/install.sh --force --secrets-only` — refresh Podman nkey secrets
4. `make converge` — remount `auth.conf`, restart stack (when `--converge` is set)

Without `--converge`, restart manually after step 3:

```bash
systemctl --user restart factory-nats factory-hub factory-telegram factory-discord factory-clipool factory-omp factory-turn-writer
```

### Flags

| Flag | When |
|------|------|
| `--yes` | Non-interactive / no TTY — required for scripts |
| `--converge` | Full stack restart via `make converge` (recommended on M₁) |
| `--ack-external-distribution` | After manual fan-out to M₂ (see below) — without it, regen exits 2 when externals exist |
| `--dry-run` | Print planned steps only |

Preview:

```bash
factory secrets reset --dry-run
```

Equivalent manual sequence (same as the CLI):

```bash
factory-acl genkeys --regenerate --yes
./deploy/install.sh --force --secrets-only
make converge
```

## External identities (M₂ fan-out)

`acl-matrix.json` declares identities with `"deploy": { "type": "external", … }`. After regen on M₁, copy new seeds to the remote host **before** restarting clients that use them.

Current externals (check matrix for live list):

| Identity | Remote host | Target path |
|----------|-------------|-------------|
| `llm-worker` | `roxabitower` | `~/.roxabi/llmcli/nkeys/llm-worker.seed` |
| `llm-operator` | `roxabitower` | `~/.roxabi/llmcli/nkeys/operator.creds` |
| `image-worker` | `roxabitower` | `~/.roxabi/imagecli/nkeys/image-worker.seed` |
| `voice-client` | `roxabitower` | `~/.voicecli/nkeys/voice-client.seed` |

Workflow:

```bash
# On M₁ — regen stops with manifest unless acknowledged
factory secrets reset --yes --ack-external-distribution --converge

# Regen prints scp lines, e.g.:
#   scp ~/.roxabi/factory/nkeys/llm-worker.seed user@roxabitower:~/.roxabi/llmcli/nkeys/llm-worker.seed

# On M₂ — install creds, restart affected units (llmCLI, voiceCLI, imageCLI)
```

Only pass `--ack-external-distribution` after seeds are copied to every external target.

## Post-recovery verification

```bash
factory ops verify
systemctl --user status 'factory-*.service'
journalctl --user -u factory-hub -u factory-nats --since "5 min ago" --no-pager
```

Smoke: send a test message through Telegram/Discord; confirm clipool/OMP health if in use.

## Future: Vaultwarden / agent-secret broker

When the secret broker lands, master-password loss is a **separate** recovery path: wipe the factory Vaultwarden data, re-create the org/collection, re-import API keys from upstream consoles. Agents (`--network=none`) hold no long-lived creds.

Design and checklist → [agent-secret-broker.md §10](../../artifacts/analyses/agent-secret-broker.md#10-recovery--clé-perdue--wipe--regen) (analysis not yet promoted).

`factory secrets reset` today covers **NATS nkeys only**; broker wipe will get its own runbook step when implemented.

## Rollback

If regen fails mid-flight, `_mode_regenerate` restores from `nkeys.bak.{epoch}/`. If regen succeeded but the stack is unhealthy:

```bash
# Restore backup (replace {epoch} with timestamp from nkeys.bak.*)
rm -rf ~/.roxabi/factory/nkeys
cp -a ~/.roxabi/factory/nkeys.bak.{epoch} ~/.roxabi/factory/nkeys
./deploy/install.sh --force --secrets-only
make converge
```

Rollback is only viable if the backup predates the incident and seeds were not compromised.