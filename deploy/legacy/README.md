# deploy/legacy/

Pre-#611 supervisord stack — kept for reference and rollback.

Replaced by `deploy/quadlet/` (Podman Quadlet units managed by systemd --user).

## Contents

- `supervisor/` — supervisord config, conf.d/ programs, and launch scripts for the
  three-process stack (`lyra-hub`, `lyra-telegram`, `lyra-discord`). These were the
  production entry points on Machine 1 before the Quadlet cutover (#611).

## Status

Superseded. The production deployment path is now:

```bash
make quadlet-install       # install Quadlet units to ~/.config/containers/systemd/
systemctl --user start lyra-hub lyra-telegram lyra-discord
```

See `deploy/quadlet/` and `docs/DEPLOYMENT-quadlet.md`.
