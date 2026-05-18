# Clipool UID Trust Model

Read this when you hit a git ownership error inside `lyra-clipool`, or when auditing
why `safe.directory` wildcards no longer appear in `git.config.tmpl`.

---

## Trust grant

`deploy/quadlet/lyra-gh.pod` carries `UserNS=keep-id:uid=1500,gid=1500`. This remaps
the host operator (uid 1000, `mickael`) to container uid 1500 (`lyra`). Bind-mounted
repos under `~/.lyra/` and `/home/lyra/projects/` are owned by uid 1000 on the host;
inside the container they appear owned by uid 1500, which is exactly the uid git runs
as. The native ownership check passes without any config override. This userns mapping
is the trust boundary — everything else follows from it.

---

## Why no `safe.directory` wildcards

A wildcard such as `directory = /home/lyra/projects/*` expresses "trust any repo under
this path regardless of owner". That is trust-by-path, a pattern in the CWE-426/427
lineage (untrusted search path). The userns remap already guarantees that only the
intended uid can place files on the bind-mount host side; the path wildcard adds no
security and widens the attack surface. Removed in issue #1149; the `[safe]` block is
absent from `deploy/lyra-gh/git.config.tmpl`.

---

## Regression catch — startup probe

`src/lyra/bootstrap/infra/git_ownership_probe.py` runs at clipool startup. It invokes
`git rev-parse HEAD` on a known bind-mounted repo (default:
`/home/lyra/projects/lyra`; overridable via `LYRA_OWNERSHIP_PROBE_PATH`). If the userns
mapping ever breaks — for example because someone removes `keep-id` from `lyra-gh.pod`
— git sees a uid mismatch and returns a non-zero exit code. The probe propagates that
exit, `_bootstrap_clipool_standalone` fails, and systemd retries per `Restart=on-failure`
up to `StartLimitBurst=5` within 60 s, then marks `lyra-clipool.service` failed. The
failure is visible in `journalctl --user -u lyra-clipool.service`; it does not cascade to
the pod, which keeps running. "Loud" here means logged loudly — operator monitoring or a
`systemctl --user status lyra-clipool.service` check is what surfaces it.

**Precondition:** the host must have `~/projects/lyra` checked out (or Syncthing-synced)
before `lyra-clipool.service` is started. The bind-mount path `/home/lyra/projects/lyra`
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

- `deploy/quadlet/lyra-gh.pod` — line `UserNS=keep-id:uid=1500,gid=1500`
- `deploy/lyra-gh/git.config.tmpl` — post-T2 state: no `[safe]` block
- `src/lyra/bootstrap/infra/git_ownership_probe.py` — startup ownership probe
- `artifacts/specs/1149-safe-directory-idmap-spec.mdx` — full rationale, Podman #24918
  empirical validation, and threat model
- ADR-055 (`docs/architecture/adr/055-quadlet-ecosystem-conventions.mdx`) — absorbed ADR-054
  Decision 2: `UserNS=keep-id:uid=1500,gid=1500` as the standard UID model for all
  Roxabi containers
