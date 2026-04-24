# Big-Bang NATS Consolidation

**Goal:** one NATS on M₁. Everyone on `roxabi.network`. No TLS, no host service, no voicecli-nats, no supervisord.

## Final target

```
                     M₁ (roxabituwer)
                     ─────────────────
                   ┌─────────────────────┐
                   │  lyra-nats Quadlet  │
                   │  :4222 (no TLS)     │
                   │  roxabi.network     │
                   │  auth.conf: lyra +  │
                   │  voicecli identities│
                   └─────────┬───────────┘
                             │
   ┌─────────────┬───────────┼───────────┬─────────────┐
   │             │           │           │             │
 lyra-hub   lyra-telegram  lyra-discord  voicecli-tts  voicecli-stt

                     DEAD:
                     ─ host nats.service (was :4222 TLS)
                     ─ voicecli-nats.container + voicecli.network
                     ─ lyra.network (renamed to roxabi.network)
                     ─ supervisord voicecli_tts / voicecli_stt
```

- One merged `auth.conf` at `~/.lyra/nkeys/auth.conf` → Podman secret `lyra-nats-auth`
- Every client: `Network=roxabi.network`, `NATS_URL=nats://lyra-nats:4222` (no `tls://`)
- voicecli workers = Quadlet, same cutover window

## Staging prep (PRs that must land on `staging` before cutover)

### lyra staging PR

1. `deploy/nats/gen-nkeys.sh` — add `--emit-merged-authconf` mode; read identities from `acl-matrix.json` (hub, telegram, discord, voice-tts, voice-stt)
2. `Makefile`:
   - `quadlet-authconf-merged` → writes `~/.lyra/nkeys/auth.conf`
   - `quadlet-secrets-install` → upload as `lyra-nats-auth` (replaces existing)
3. `deploy/quadlet/lyra-nats.container`:
   - `PublishPort=127.0.0.1:4222:4222`
   - `Network=roxabi.network`
   - Drop TLS certs volume + TLS args from `Exec=`
   - `Secret=lyra-nats-auth,type=mount,target=/etc/nats/nkeys/auth.conf,mode=0444`
4. `deploy/quadlet/lyra-{hub,telegram,discord}.container`:
   - `Network=lyra.network` → `Network=roxabi.network`
   - `NATS_URL=nats://lyra-nats:4222` (drop `tls://`)
5. `deploy/quadlet/lyra.network` — rename file to `roxabi.network`, update label
6. Remove client-side TLS env (`NATS_TLS_CA`, etc.) from hub/adapter env examples

### voiceCLI staging PR

1. `rm deploy/quadlet/voicecli-nats.container`
2. `rm deploy/quadlet/voicecli.network`
3. `deploy/quadlet/voicecli-{tts,stt}.container`:
   - `Network=voicecli.network` → `Network=roxabi.network`
   - `NATS_URL=nats://voicecli-nats:4222` → `NATS_URL=nats://lyra-nats:4222`
   - Drop `voicecli-nats-auth` secret mount (not needed on clients)
   - Keep `voicecli-nats-{tts,stt}` seed secret mounts
4. `Makefile` — drop voicecli-nats install + drop `voicecli-nats-auth` secret creation

Merge order doesn't matter — cutover reconciles.

## Pre-window (on M₁, same day, before T+0)

```bash
# 1. Pull latest staging on both repos
cd ~/projects/lyra      && git pull --ff-only origin staging
cd ~/projects/voiceCLI  && git pull --ff-only origin staging

# 2. Pre-pull NATS image (avoid blocking download mid-cutover)
podman pull docker.io/library/nats:2.10.29-alpine

# 3. Build voicecli image locally (ADR-055 D1)
cd ~/projects/voiceCLI && podman build -t localhost/voicecli:latest .

# 4. Relocate voicecli seeds to owner path (ADR-055 D4) — cp, not mv
mkdir -p ~/.voicecli/nkeys
cp ~/.lyra/nkeys/voice-tts.seed ~/.voicecli/nkeys/
cp ~/.lyra/nkeys/voice-stt.seed ~/.voicecli/nkeys/
chmod 600 ~/.voicecli/nkeys/*.seed

# 5. Render merged auth.conf (reads voice-* pubkeys from new seeds)
cd ~/projects/lyra && make quadlet-authconf-merged
# Verify: ~/.lyra/nkeys/auth.conf contains hub, telegram, discord, voice-tts, voice-stt blocks

# 6. Install shared deploy-lib SSoT (ADR-055 D5)
make quadlet-install-deploy-lib

# 7. Delete stale Podman secrets from prior attempts
podman secret rm voicecli-tts.seed voicecli-stt.seed 2>/dev/null || true
podman secret rm voicecli-nats-auth 2>/dev/null || true
```

## Cutover sequence (linear, no decisions)

```
T+0:00  Stop all clients
          systemctl --user stop lyra-hub lyra-telegram lyra-discord
          supervisorctl stop voicecli_tts voicecli_stt

T+0:01  Kill host NATS
          sudo systemctl stop nats.service
          sudo systemctl disable nats.service
          ss -tlnp | grep ':4222 '   # must be empty

T+0:02  Stop old lyra-nats Quadlet (was on :4223)
          systemctl --user stop lyra-nats.service

T+0:03  Install new Quadlet units — lyra
          cd ~/projects/lyra
          make quadlet-secrets-install   # uploads lyra-nats-auth + lyra seed secrets
          make quadlet-install           # drops new .container files + roxabi.network

T+0:04  Install new Quadlet units — voicecli
          cd ~/projects/voiceCLI
          make quadlet-secrets-install   # uploads voicecli-nats-{tts,stt} seeds
          make quadlet-install           # drops voicecli-{tts,stt}.container

T+0:05  Reload systemd, drop old network
          systemctl --user daemon-reload
          podman network rm lyra.network voicecli.network 2>/dev/null || true

T+0:06  Start NATS
          systemctl --user start lyra-nats.service
          sleep 3
          ss -tlnp | grep ':4222 '   # must show podman/rootlessport
          systemctl --user status lyra-nats.service --no-pager

T+0:07  Start lyra clients (dependency order)
          systemctl --user start lyra-hub.service
          sleep 2
          systemctl --user start lyra-telegram.service lyra-discord.service

T+0:08  Start voicecli workers
          systemctl --user start voicecli-tts.service voicecli-stt.service

T+0:09  Verify
          systemctl --user status 'lyra-*.service' 'voicecli-*.service' --no-pager | grep -E 'Active|Main PID'
          journalctl --user --since "2 min ago" | grep -iE 'nats|connect|auth|error' | grep -v debug

T+0:10  Smoke test
          - Telegram /ping  → reply
          - Discord /ping   → reply
          - Voice note to bot → transcript reply
```

## Post-cutover cleanup (same day)

```bash
# Remove supervisord voicecli confs
supervisorctl stop voicecli_tts voicecli_stt 2>/dev/null || true
rm -f ~/projects/supervisor/conf.d/voicecli_{tts,stt}.conf

# Archive old host NATS config (keep for 1 week reference)
sudo cp /etc/nats/nkeys/auth.conf /root/auth.conf.bak-$(date +%Y%m%d)

# Verify final state
podman network ls       # roxabi.network only (no lyra.network, no voicecli.network)
podman secret ls        # lyra-nats-auth, lyra-nkey-*, voicecli-nats-{tts,stt}
systemctl --user list-units 'lyra-*' 'voicecli-*'
sudo systemctl status nats.service    # inactive (disabled)
```

## Rollback (if NATS fails at T+0:06)

Bots are already stopped at this point — rollback is purely recovery; no user-visible regression beyond the cutover window.

```bash
# 1. Stop any partially-started Quadlet units
systemctl --user stop 'lyra-*' 'voicecli-*'

# 2. Restore old nats.container from git
git show HEAD~1:deploy/quadlet/nats.container > /tmp/nats.container
cp /tmp/nats.container ~/.config/containers/systemd/nats.container
systemctl --user daemon-reload

# 3. Re-enable host NATS
sudo systemctl enable --now nats.service

# 4. Revert both PRs on M₁ (lyra + voiceCLI repos)
#    In each repo: git revert HEAD && make quadlet-install
git revert HEAD   # lyra
make quadlet-install

# 5. Restart bots via old Quadlet units
systemctl --user start lyra-hub.service lyra-telegram.service lyra-discord.service
```

## References

- [ADR-055 — Quadlet ecosystem conventions](../architecture/adr/055-quadlet-ecosystem-conventions.mdx) — original staged plan; this doc collapses Phases 3+4 and drops TLS inside `roxabi.network`
- [ADR-054 — Podman secrets](../architecture/adr/054-podman-secrets.mdx)
- [ADR-046 — declarative auth.conf provisioning](../architecture/adr/046-nkey-provisioning-declarative-authconf.mdx)
