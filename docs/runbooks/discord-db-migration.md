# Runbook — discord.db → named volume (#1721)

One-time migration: move `discord.db` from the hub-shared `factory-data.volume` to Discord-private volume `factory-discord-data`.

Run **before** the converge that applies the new `factory-discord.container` mount. Missing the copy loses active thread→session links.

## Step A — Copy discord.db

```bash
systemctl --user stop factory-discord || true

podman run --rm \
  -v factory-discord-data:/dst \
  -v ~/.roxabi/factory:/src:ro \
  alpine cp /src/discord.db /dst/discord.db

podman run --rm -v factory-discord-data:/data:ro alpine \
  sh -c 'ls -lh /data/discord.db && echo OK'
```

## Step B — Converge

```bash
make converge
```

## Step C — Verify Discord adapter

```bash
systemctl --user status factory-discord
journalctl --user -u factory-discord -n 20 | grep -E "ThreadStore|discord\.db|error" || true
podman inspect factory-discord --format '{{range .Mounts}}{{.Name}} → {{.Destination}}{{"\n"}}{{end}}' | grep discord || true
grep "factory-data.volume" ~/.config/containers/systemd/factory-telegram.container || echo "OK — telegram has no shared data volume"
```

## Step D — Verify turns.db ownership (#1049)

Canonical `~/.roxabi/factory/turns.db` is written by `factory-turn-writer`, read ro by `factory-hub` (ADR-075). Confirm turn-writer healthy before removing any legacy adapter-side copy:

```bash
systemctl --user status factory-turn-writer
journalctl --user -u factory-turn-writer -n 20
lsof ~/.roxabi/factory/turns.db 2>/dev/null || echo "No process has turns.db open (expected)"
```