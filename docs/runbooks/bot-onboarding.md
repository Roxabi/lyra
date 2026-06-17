# Runbook — Bot onboarding

## (a) No manual splicing

Tracked templates `deploy/quadlet/factory-{telegram,discord}.container.tmpl` are **pure templates** — they contain `{{bot_secrets}}` only. Per-bot `Secret=factory-bot-<platform>-<bot_id>` lines are generated at install by `tools/render_quadlet.py` from `BotStore` (`~/.roxabi/factory/config.db`).

**Never edit `~/.config/containers/systemd/factory-{telegram,discord}.container` directly** — the next `make quadlet-install` overwrites it. Manual splice caused the #1369 crash-loop cascade.

## (b) Add a new bot

```bash
factory agent <platform> add <bot_id> --agent <agent>
factory bot secret install <platform> <bot_id>
# webhook variant:
factory bot secret install <platform> <bot_id>-webhook

make quadlet-install
systemctl --user status factory-<platform>
```

`make quadlet-install` runs `factory bot init`, renders secrets, `daemon-reload`, and restarts the adapter. **`systemctl restart` is mandatory after `Secret=` changes** — `daemon-reload` alone does not refresh tmpfs mounts in a running container.

## (c) CI guard

- `tools/check_quadlet_template_purity.sh` — no `Secret=factory-bot-*` or `BEGIN/END` splice blocks in `.tmpl` files
- `make quadlet-lint` · `.github/workflows/quadlet-lint.yml`

## (d) Multi-host caveat

`config.toml` may be Syncthing-synced across hosts, so it lists **all bots**. `render_quadlet.py` emits `Secret=` for every bot in `BotStore` on **each** host.

Podman secrets are **host-local**. Missing secret → immediate start failure:

```
Error: looking up secret name "factory-bot-telegram-<bot_id>": no such secret
```

**Mitigation:** run `factory bot secret install` on every host that runs the platform adapter before `make quadlet-install`. Host roles are declared in your infra manifest (e.g. `hosts.toml`); only install adapter secrets on hosts that run `factory-telegram` / `factory-discord`.

## (e) Secret class

Bot tokens use class `bot-token` in `deploy/secrets-policy.toml`:

```
factory-bot-{platform}-{bot_id}
factory-bot-{platform}-{bot_id}-webhook   # when webhook_enabled
```

Not in `deploy/quadlet.toml` `required_secrets` (rendered via `{{bot_secrets}}`). No canonical seed file under `~/.roxabi/factory/`.