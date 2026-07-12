# Runbook — Dashboard control-plane auth bootstrap (ADR-103)

## First install

1. Ensure `auth.db` path is writable (`~/.roxabi/factory/auth.db` or `FACTORY_AUTH_DB`).
2. Set env on the web/dashboard process (password **required** when email is
   set — never auto-generated, never written to logs):

   ```bash
   FACTORY_DASHBOARD_BOOTSTRAP_ADMIN_EMAIL=you@example.com
   FACTORY_DASHBOARD_BOOTSTRAP_ADMIN_PASSWORD='…strong…'
   ```

3. Start `factory adapter web` (or unified `factory start`). On first connect,
   ControlPlaneStore creates the admin if none exists. Missing password with
   email set fails startup with a clear error.
4. Open the SPA → **Sign in** with that email/password.
5. **Invite** users: `POST /api/bff/auth/invites` (admin session) or future UI.
6. Each user: **Link accounts** → `/link <code>` on Telegram **and** Discord.
7. Admin grants agent USE (CLI or Users page) — grants land on platform ids.

## Cookies

- Production: Secure cookies (HTTPS / Tailscale TLS).
- Local HTTP: `FACTORY_DASHBOARD_COOKIE_INSECURE=1`.

## Chat readiness

`chat_ready` ⇔ linked Telegram **and** Discord. Console works without link;
TG/DC agent turns refuse until dual-linked (`PlatformLinkMiddleware`).

## Health

Liveness / HealthCmd remains unauthenticated. Do not put auth on `/healthz`.

## Rollback (emergency)

Unset bootstrap env and stop requiring principal only after a deliberate
maintenance window — hub `_wrap` fails closed without principal stamps.
Prefer fixing session secret / admin password over disabling auth.
