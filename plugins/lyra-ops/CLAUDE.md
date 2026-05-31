# CLAUDE.md — lyra-ops plugin

## What this is

A Claude Code plugin that provides operational skills for Lyra in production.
It is **not** lyra runtime code — it is external tooling that runs from a dev/ops
machine and reaches production exclusively via SSH + `make remote`.

Install path: `plugins/lyra-ops/` inside the lyra repo. Published as a
Claude Code plugin via `roxabi-plugins` (or loaded locally from this path).

## Contract with production

- All production access goes through `make remote <unit> <action>` or explicit
  `ssh $H "..."` calls — never direct Python imports or lyra source references.
- `$H` := `DEPLOY_HOST` read from `~/projects/lyra/.env` on the local machine.
- Production runtime: Podman Quadlet (rootless systemd --user units).
- Health endpoint: `http://localhost:8443/health/detail` (loopback on `$H`,
  bearer token from `~/.lyra/secrets/health_secret`).
- Logs: `journalctl --user -u <unit>` on `$H`. In-container files via
  `podman exec lyra-hub`.

## Isolation rules

- ¬import lyra Python source from any skill here.
- ¬write to production files — read-only access except when executing an
  explicitly user-confirmed remediation command.
- ¬store credentials in this plugin — secrets stay on `$H` or in `.env`.
- Skills here are ops surfaces: they observe, diagnose, and execute targeted
  make/ssh commands. Code changes belong to a `/dev` workflow, not here.

## Skill responsibilities

`/lyra-debug` — full diagnostic cycle: status → health endpoint → logs →
root-cause diagnosis → remediation options (DP) → recovery verification.
Covers both degraded and fully-down scenarios across all Lyra Quadlet units
(authoritative list: `deploy/quadlet.toml`).

## Adding skills

New skills go under `skills/<name>/SKILL.md`. Each skill must declare its
`allowed-tools` explicitly. Ops skills: `Bash, Read, Glob, Grep` only —
¬Write, ¬Edit (production files are not writable from here).
