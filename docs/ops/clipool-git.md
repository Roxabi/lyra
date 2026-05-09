# Clipool — Git Behavior Reference

Operator notes on how `git` behaves inside the `lyra-clipool` container. Not a runbook — see `gh-key-rotation.md` for procedures.

## SSH → HTTPS URL rewrite

`deploy/lyra-gh/git.config.tmpl` is loaded as `GIT_CONFIG_GLOBAL` inside `lyra-clipool`. It contains:

```ini
[url "https://github.com/"]
  insteadOf = git@github.com:
  insteadOf = ssh://git@github.com/
```

These rules silently rewrite SSH-form GitHub remote URLs to HTTPS at command time. This is intentional: the container runs with `ReadOnly=true` and no `~/.ssh` mount, so SSH-form remotes would fail with "Host key verification failed".

**Operator guidance.** When adding new git remotes inside the container, always use HTTPS form (`https://github.com/<org>/<repo>.git`). SSH-form URLs will still work (they are rewritten transparently), but the rewrite may surprise operators who expect SSH authentication.

## Authentication

Git uses HTTPS + a credential helper (`/opt/lyra-gh/git-credential-lyra-gh`) which fetches a fresh GitHub App installation token from the `lyra-gh-helper` sidecar over a Unix socket. Tokens have a 1 h TTL and are refreshed proactively.

→ `docs/ops/gh-key-rotation.md` — rotating the App PEM.

## Identity

Commits made from inside the container are attributed to:

```
lyra[bot] <lyra-bot@users.noreply.github.com>
```

Set image-baked in `git.config.tmpl`. Per-agent attribution is tracked as a follow-up (issue #1150).

## Safe directory

The image-baked config trusts repos under `/home/lyra/projects/*` via `safe.directory`. This works around uid drift between host (1000) and container (1500) on bind-mounted Syncthing trees. Tracked for replacement with proper `idmap` in issue #1149.

## Cross-References

- [`deploy/lyra-gh/git.config.tmpl`](../../deploy/lyra-gh/git.config.tmpl) — the config loaded as `GIT_CONFIG_GLOBAL`
- [`deploy/quadlet/lyra-clipool.container`](../../deploy/quadlet/lyra-clipool.container) — env wiring (`GIT_CONFIG_GLOBAL`, mounts)
- [`docs/ops/gh-key-rotation.md`](gh-key-rotation.md) — PEM rotation runbook
