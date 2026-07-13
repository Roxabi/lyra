# Runbook — Dashboard control-plane auth bootstrap (ADR-103)

> **ADR-103 hub IdP:** durable identity opens on **hub only**. Dashboard is a thin
> BFF (NATS `factory.dashboard.auth.*`). Do **not** mount `factory-data` on the
> dashboard unit. Migration:
> [`dashboard-auth-hub-idp-migration.md`](../../artifacts/goal/dashboard-auth-hub-idp-migration.md).

## Disable / re-enable (ops)

### Disable (Slice 0 — durable control is git)

```bash
# Host one-time (M₁) — stop crash-loop immediately
systemctl --user stop factory-dashboard.service || true
systemctl --user reset-failed factory-dashboard.service 2>/dev/null || true
# Optional emergency pin (if used, MUST unmask on re-enable):
# systemctl --user mask factory-dashboard.service

# After merge of disabled=true in quadlet.toml:
make converge
# Verify unit file pruned / not in restart set:
test ! -f ~/.config/containers/systemd/factory-dashboard.container || true
podman ps -a --filter name=factory-dashboard
# Hub/NATS still green:
curl -sS http://127.0.0.1:8443/health
```

| Control | Scope |
|---------|--------|
| `quadlet.toml` `disabled = true` | **Git SSoT** — converge/prune skips unit |
| `systemctl mask` | **Host-local** only; not durable across clean hosts |

### Re-enable (Slice 4 only)

```bash
# 1. PR: disabled = false (or remove key) after Slices 2–3 on staging-svc
# 2. If ever masked:
systemctl --user unmask factory-dashboard.service
# 3.
make converge
systemctl --user is-active factory-dashboard
# 4. Smoke: Tailnet :8765 — unauth /api/bff/auth/me → 401; /healthz public
```

## Target install (post Slice 1–4 — hub IdP)

1. Ensure hub can write `auth.db` (`factory-data.volume` / `FACTORY_AUTH_DB`).
2. Bootstrap admin env on **hub** (Slice 1 renames may apply — see migration):

   ```bash
   # provisional names until Slice 1 lands
   FACTORY_DASHBOARD_BOOTSTRAP_ADMIN_EMAIL=you@example.com
   FACTORY_DASHBOARD_BOOTSTRAP_ADMIN_PASSWORD='…strong…'
   ```

3. Thin BFF on dashboard resolves sessions via **hub RPC** (no local ControlPlaneStore).
4. SPA → Sign in; invite; link TG+DC; grants on platform ids.

## Dual-open (removed)

`WebAdapter.astart` no longer calls `open_control_plane_store`. Bootstrap helper
`open_control_plane_for_dashboard` **raises** if invoked. Local pytest may inject
a `ControlPlaneDirectory` into `create_app(..., control_plane=store)` without
opening auth.db in the production composition root.

## Cookies

- Production: Secure cookies (HTTPS / Tailscale TLS).
- Local HTTP: `FACTORY_DASHBOARD_COOKIE_INSECURE=1`.

## Chat readiness

`chat_ready` ⇔ linked Telegram **and** Discord. Console works without link;
TG/DC agent turns refuse until dual-linked (`PlatformLinkMiddleware`).

## Health

Public `GET /healthz` → `{"status":"ok"}` (no principal). HealthCmd must probe
`/healthz` only — never `/api/agents` (auth-gated).
(Slice 3: HealthCmd must not require `/api/agents` 200.)

## Rollback (emergency)

Prefer hub/session secret fix over disabling auth. Interim console off =
`disabled = true` + stop (this Slice 0 posture).
