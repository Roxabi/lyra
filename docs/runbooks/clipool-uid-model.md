# Clipool — Git Behavior & UID Trust Model

Operator reference for how `git` behaves inside the `factory-clipool` container, and
why `safe.directory` wildcards no longer appear in `git.config.tmpl`. Read this when you
hit a git ownership error inside `factory-clipool`, or when adding remotes / auditing
identity. For rotation procedures see [gh-key-rotation.md](gh-key-rotation.md).

---

## SSH → HTTPS URL rewrite

`deploy/factory-gh/git.config.tmpl` is loaded as `GIT_CONFIG_GLOBAL` inside `factory-clipool`. It contains:

```ini
[url "https://github.com/"]
  insteadOf = git@github.com:
  insteadOf = ssh://git@github.com/
```

These rules silently rewrite SSH-form GitHub remote URLs to HTTPS at command time. This is intentional: the container runs with `ReadOnly=true` and no `~/.ssh` mount, so SSH-form remotes would fail with "Host key verification failed".

**Operator guidance.** When adding new git remotes inside the container, always use HTTPS form (`https://github.com/<org>/<repo>.git`). SSH-form URLs will still work (they are rewritten transparently), but the rewrite may surprise operators who expect SSH authentication.

---

## Authentication

Git uses HTTPS + a credential helper (`/opt/factory-gh/git-credential-factory-gh`) which fetches a fresh GitHub App installation token from the `factory-gh-helper` sidecar over a Unix socket. Tokens have a 1 h TTL and are refreshed proactively. See [gh-key-rotation.md](gh-key-rotation.md) for rotating the App PEM.

---

## Identity

Commits made from inside the container are attributed to:

```
lyra[bot] <lyra-bot@users.noreply.github.com>
```

Set image-baked in `git.config.tmpl`. Per-agent attribution is tracked as a follow-up (issue #1150).

---

## Trust grant (UID model)

`deploy/quadlet/factory-gh.pod` carries `UserNS=keep-id:uid=1500,gid=1500`. This remaps
the host operator (uid 1000, `mickael`) to container uid 1500 (`lyra`). Bind-mounted
repos under `~/.roxabi/factory/` and `/home/factory/projects/` are owned by uid 1000 on the host;
inside the container they appear owned by uid 1500, which is exactly the uid git runs
as. The native ownership check passes without any config override. This userns mapping
is the trust boundary — everything else follows from it.

---

## Why no `safe.directory` wildcards

A wildcard such as `directory = /home/factory/projects/*` expresses "trust any repo under
this path regardless of owner". That is trust-by-path, a pattern in the CWE-426/427
lineage (untrusted search path). The userns remap already guarantees that only the
intended uid can place files on the bind-mount host side; the path wildcard adds no
security and widens the attack surface. Removed in issue #1149; the `[safe]` block is
absent from `deploy/factory-gh/git.config.tmpl`.

---

## Regression catch — startup probe

`src/factory/bootstrap/infra/git_ownership_probe.py` runs at clipool startup. It invokes
`git rev-parse HEAD` on a known bind-mounted repo (default:
`/home/factory/projects/roxabi-factory`; overridable via `FACTORY_OWNERSHIP_PROBE_PATH`). If the userns
mapping ever breaks — for example because someone removes `keep-id` from `factory-gh.pod`
— git sees a uid mismatch and returns a non-zero exit code. The probe propagates that
exit, `_bootstrap_clipool_standalone` fails, and systemd retries per `Restart=on-failure`
up to `StartLimitBurst=5` within 60 s, then marks `factory-clipool.service` failed. The
failure is visible in `journalctl --user -u factory-clipool.service`; it does not cascade to
the pod, which keeps running. "Loud" here means logged loudly — operator monitoring or a
`systemctl --user status factory-clipool.service` check is what surfaces it.

**Precondition:** the host must have `~/projects/roxabi-factory` checked out (or Syncthing-synced)
before `factory-clipool.service` is started. The bind-mount path `/home/factory/projects/roxabi-factory`
inside the container is the probe target; if it does not exist on the host, the probe
fails fast with "target directory does not exist" — correct behavior, but confusing on
first provision. Add this to the operator's provision checklist.

---

## Why not idmap

Podman's `Volume=...:idmap` option remaps mount-level ownership without a userns and is
the more surgical tool. It is unavailable here: `idmap` requires rootful Podman because
it calls `mount_setattr(MOUNT_ATTR_IDMAP)`, which the kernel rejects with
`Operation not permitted` for rootless processes. Clipool runs as a rootless systemd
user unit. The `keep-id` userns is the correct alternative for rootless containers.
Upstream tracking: https://github.com/containers/podman/issues/24918

---

## References

- [`deploy/factory-gh/git.config.tmpl`](../../deploy/factory-gh/git.config.tmpl) — config loaded as `GIT_CONFIG_GLOBAL`; post-T2 state: no `[safe]` block
- [`deploy/quadlet/factory-clipool.container`](../../deploy/quadlet/factory-clipool.container) — env wiring (`GIT_CONFIG_GLOBAL`, mounts)
- [`deploy/quadlet/factory-gh.pod`](../../deploy/quadlet/factory-gh.pod) — line `UserNS=keep-id:uid=1500,gid=1500`
- `src/factory/bootstrap/infra/git_ownership_probe.py` — startup ownership probe
- [gh-key-rotation.md](gh-key-rotation.md) — App PEM rotation runbook
- `artifacts/specs/1149-safe-directory-idmap-spec.mdx` — full rationale, Podman #24918
  empirical validation, and threat model
- ADR-055 (`docs/architecture/adr/055-quadlet-ecosystem-conventions.mdx`) — absorbed ADR-054
  Decision 2: `UserNS=keep-id:uid=1500,gid=1500` as the standard UID model for all
  Roxabi containers
