# Runbook — Quadlet diagnostic & remediation

## Quick checks

```bash
systemctl --user status 'factory-*'
podman ps --filter 'name=factory'
journalctl --user -u factory-hub -f
curl -s http://127.0.0.1:8222/varz | python3 -m json.tool | grep -E '"connections"|"version"'
podman secret ls
podman exec factory-hub cat /run/secrets/factory-nats-hub.seed | head -c 4
podman auto-update --dry-run
systemctl --user status podman-auto-update.timer
```

Hub health (loopback): `curl -fsS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8443/health/detail`

## Common issues

| Symptom | Likely cause | Fix |
|---|---|---|
| `factory-hub` fails to start | Missing `factory-nats-hub` secret | `./deploy/install.sh --secrets-only` |
| NATS auth failure | Stale `auth.conf` | `make nats-regen-authconf` + restart NATS |
| `factory-gh-helper` fails | Missing `factory-gh-pem` | Install PEM secret |
| Restart loop | Crash on boot | `journalctl --user -u <svc> -n 50` |
| Auto-update not pulling | Timers inactive | Remediation below |

## Auto-update remediation

All three timers must be active for hands-off deploys:

```bash
make quadlet-sync-install
systemctl --user daemon-reload
systemctl --user enable --now podman-auto-update.timer
systemctl --user enable --now factory-quadlet-sync.timer
systemctl --user enable --now factory-post-autoupdate.timer
systemctl --user restart podman-auto-update.timer

systemctl --user is-enabled podman-auto-update.timer factory-quadlet-sync.timer factory-post-autoupdate.timer
systemctl --user is-active  podman-auto-update.timer factory-quadlet-sync.timer factory-post-autoupdate.timer
podman auto-update --dry-run
```

Expected: seven `registry`-tracked factory containers (`factory-hub`, `factory-telegram`, `factory-discord`, `factory-clipool`, `factory-gh-helper`, `factory-turn-writer`, `factory-blobstore`). `factory-nats` is digest-pinned — no autoupdate label.

## Pitfall: HealthCmd double-quotes

Quadlet drops closing `"` in `HealthCmd=pgrep -f "..."` → perpetual `unhealthy` (#1370).

Use `/proc/1/cmdline` instead:

```ini
HealthCmd=grep -q telegram /proc/1/cmdline
```

Enforced by `quadlet-lint.yml` CI check.