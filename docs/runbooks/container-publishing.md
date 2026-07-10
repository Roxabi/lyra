# Container Publishing Pattern

## Overview

factory containers are built and published to GHCR from this repo via a **bake pipeline**
(`docker/bake-action` + `docker-bake.hcl` — see `.github/workflows/publish.yml`). Other Roxabi
projects typically use a shared reusable workflow at
`Roxabi/.github/.github/workflows/publish-container.yml@staging`. Registry convention:
`ghcr.io/roxabi/<project>`. Two triggers drive publishing: a push to `staging` produces floating
tags for pre-release validation on M₁ (`:staging` + `:staging-svc` for factory); a release-please tag
`<component>/vX.Y.Z` on `main` produces semver pins (`:X.Y.Z`, `:X`, `:latest` where applicable).

---

## Dockerfile conventions

- **Multi-stage build** — at minimum a build stage and a runtime stage; never ship build tools in
  the final image.
- **Pinned base image** — use an explicit minor version (e.g. `python:3.12.10-slim`); digest
  pinning is preferred for the runtime stage in high-security contexts.
- **Pinned package layers** — pin `uv` by version in the build stage; `apt-get` layers must use
  `--no-install-recommends` and clean lists in the same `RUN` step.
- **Explicit non-root UID** — create a dedicated system user with a fixed numeric UID. factory uses
  UID/GID 1500 (`factory`). Never run as root or rely on the default `nobody` UID.
- **HEALTHCHECK** — must exit 0 on healthy, non-zero on unhealthy. factory uses
  `HEALTHCHECK CMD factory config validate`. The command must be available in the final stage.

  > **Note:** `HEALTHCHECK` requires Docker manifest format (v2 schema 2). OCI image manifests
  > silently drop this instruction. The reusable workflow sets `oci-mediatypes=false` on the
  > `docker/build-push-action` step to force Docker v2 schema 2, so `HEALTHCHECK` is preserved
  > in the published image. No action needed in the Dockerfile or caller workflow.

  > **svc-runtime (`staging-svc`) requires `config.toml` bind-mount:** `factory config validate`
  > opens `config.toml` from `WORKDIR /app` and exits 1 on `FileNotFoundError`. In production
  > Quadlets the file is bind-mounted, so this is fine. Running `docker run` without the mount
  > (local dev, CI smoke tests) will immediately mark the container unhealthy — this is expected
  > behaviour, not a bug.
- **OCI labels** — do not set `org.opencontainers.image.*` labels in the Dockerfile. They are
  injected at build time by `docker/metadata-action@v5` in the reusable workflow, ensuring labels
  always match the actual pushed tag and commit SHA.

---

## Caller workflows

### factory — (`roxabi-factory`) — bake pipeline

SSoT: `.github/workflows/publish.yml`. Builds two Dockerfile targets (`agent-runtime`,
`svc-runtime`) and pushes `:staging` / `:staging-svc` on branch push, semver tags on release.

```yaml
name: publish
on:
  push:
    branches: [staging]
    tags: ['factory/v*']
permissions:
  contents: read
  packages: write
jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: docker/setup-buildx-action@v4
      - uses: docker/login-action@v4
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: Compute image tags
        id: tags
        run: |
          REG=ghcr.io/roxabi/factory
          # staging → :staging + :staging-svc; tags factory/v* → semver (see full workflow)
      - uses: docker/bake-action@v7
        with:
          files: docker-bake.hcl
          push: true
          set: |
            ${{ steps.tags.outputs.bake_set }}
```

See the full workflow for tag computation and the post-build `svc-runtime` binary gate.

### Other Roxabi projects — reusable workflow template

The reusable workflow accepts four inputs:

| Input | Required | Default | Description |
|---|---|---|---|
| `image_name` | yes | — | Full registry path, e.g. `ghcr.io/roxabi/voicecli` |
| `release_please_component` | yes | — | Component name as used in the release-please tag, e.g. `voicecli` |
| `dockerfile_path` | no | `./Dockerfile` | Path to the Dockerfile relative to the build context |
| `build_context` | no | `.` | Docker build context path |

```yaml
jobs:
  publish:
    uses: Roxabi/.github/.github/workflows/publish-container.yml@staging
    with:
      image_name: ghcr.io/roxabi/<project>
      release_please_component: <project>
```

Callers MUST pin `@staging` during beta (entire fleet on `staging`; `main` unused). Satellite
images publish as `:staging`; Quadlets pull `:staging` via `podman auto-update`. Do not use a
floating `v1` infra branch — it was retired 2026-07-10.

---

## Quadlet `Image=` convention

Two rules govern how Quadlet units reference the published image:

**Rule A — production (post-first-release):** pin to the immutable semver tag produced by
release-please. This ensures a daemon-reload never silently pulls a different layer.

```ini
# Before (floating staging tag):
Image=ghcr.io/roxabi/factory:staging

# After first release cut (semver pin):
Image=ghcr.io/roxabi/factory:1.0.0
```

**Rule B — pre-release / staging validation:** use `:staging` so that each push to the
`staging` branch is picked up on the next pull without a Quadlet edit.

Switching between the two is a one-line edit to the `.container` file followed by
`systemctl --user daemon-reload`.

---

## Auto-Update Flow

Since #929, prod (M₁) uses `podman auto-update` to automatically pull new images and restart
containers. Auto-deploy holds *provided the three timers described below are enabled and active
on the production host. See `docs/runbooks/quadlet-diagnostic.md` (auto-update remediation) if any timer
is inactive.

### Three-timer model

Three systemd `--user` timers on M₁ drive fully hands-off deploys, each firing every 5 minutes:

| Timer | Role |
|---|---|
| `podman-auto-update.timer` | Polls GHCR digests for containers labelled `io.containers.autoupdate=registry`; pulls and restarts on new digest. Static apt unit; drop-in sets `OnCalendar=*:0/5`. |
| `factory-quadlet-sync.timer` | `git pull --ff-only origin staging`; on `deploy/**` changes runs `make converge` (Quadlet unit/template updates). |
| `factory-post-autoupdate.timer` | Detects image-digest changes produced by `podman-auto-update`; on change triggers the full `make converge` (auth.conf regen + secret refresh + restarts). |

All three must be enabled and active for fully automatic deploys. Check with
`systemctl --user is-enabled` and `is-active` for each timer name.

### How it works

1. CI merges to `staging` → `publish.yml` pushes `ghcr.io/roxabi/<project>:staging`
2. `podman-auto-update.timer` fires every 5 minutes on M₁
3. `podman auto-update` checks GHCR digest for each container with `AutoUpdate=registry` label
4. New digest detected → pulls image, restarts the container

### Prerequisites

- **GHCR auth:** `podman login ghcr.io` on M₁ (credential stored in
  `~/.config/containers/auth.json`). Uses a GitHub PAT or OAuth token with `read:packages`.
- **AutoUpdate label:** each `.container` file must have
  `Label=io.containers.autoupdate=registry` in the `[Container]` section.
- **Timer drop-in:** `~/.config/systemd/user/podman-auto-update.timer.d/override.conf` sets
  `OnCalendar=*:0/5` (every 5 minutes, no randomized delay).

### Containers managed

| Container | Image | AutoUpdate |
|---|---|---|
| `factory-nats` | pinned by digest | none (pinned) |
| `factory-hub` | `ghcr.io/roxabi/factory:staging-svc` | registry |
| `factory-telegram` | `ghcr.io/roxabi/factory:staging-svc` | registry |
| `factory-discord` | `ghcr.io/roxabi/factory:staging-svc` | registry |
| `factory-dashboard` | `ghcr.io/roxabi/factory:staging-svc` | registry |
| `factory-clipool` | `ghcr.io/roxabi/factory:staging` | registry |
| `factory-gh-helper` | `ghcr.io/roxabi/factory:staging` | registry |
| `factory-turn-writer` | `ghcr.io/roxabi/factory:staging-svc` | registry |
| `factory-blobstore` | `ghcr.io/roxabi/factory:staging-svc` | registry |
| `factory-omp` | `ghcr.io/roxabi/factory:staging` | registry |

> `factory-nats` is pinned by digest and carries no `io.containers.autoupdate=registry` label — it is intentionally excluded from the auto-update cycle; bump manually.
> voiceCLI units are managed by the voiceCLI repo and its own Quadlet manifests — see that repo for its auto-update configuration.

### Verify

```bash
# Check timer is active
systemctl --user is-active podman-auto-update.timer

# Dry-run — lists containers and whether an update is pending
podman auto-update --dry-run

# Force an immediate update check
podman auto-update
```

### Caveats

- `podman auto-update` restarts containers independently — it does **not** respect Quadlet
  `After=` ordering. Adapters may restart before hub. NATS reconnect logic handles this.
- If the GHCR token expires, auto-update silently stops pulling. Check with
  `podman login --get-login ghcr.io`.
- Podman does not auto-rollback on startup failure. A bad image enters a restart loop
  (`Restart=on-failure`). Check with `podman ps` or `journalctl --user -u <unit>`.
- **Parallel publish inconsistency window (known):** `publish` (`:staging`) and `publish-svc`
  (`:staging-svc`) jobs run in parallel — if one fails, the other may still push. During that
  window, autoupdate can pull one variant while the other is stale. Accepted risk at staging;
  long-term fix is a single `docker buildx bake` job that pushes both targets atomically —
  tracked in #1325.

---

## M1 manual pull + restart (fallback)

If auto-update is disabled or you need an immediate deploy without waiting for the timer:

CI publishes both tags in parallel: `:staging` is used by `factory-clipool` and `factory-gh-helper`; `:staging-svc` is used by `factory-hub`, `factory-telegram`, `factory-discord`, `factory-dashboard`, `factory-turn-writer`, and `factory-blobstore`. Both must be pulled for a complete manual refresh.

```bash
podman pull ghcr.io/roxabi/factory:staging
podman pull ghcr.io/roxabi/factory:staging-svc
systemctl --user daemon-reload
systemctl --user restart factory-hub factory-telegram factory-discord factory-clipool \
  factory-turn-writer factory-gh-helper factory-blobstore
```

Verify all seven service units are healthy (converge order matches `make converge` step 7):

```bash
systemctl --user is-active \
  factory-hub factory-telegram factory-discord factory-clipool \
  factory-turn-writer factory-gh-helper factory-blobstore
curl -fsS localhost:8443/health
```

`is-active` prints `active` for each unit on success. The health endpoint is served by
`factory-hub` on `127.0.0.1:8443` (published via PublishPort in the Quadlet unit).

> `factory-nats` is excluded from this restart sequence — it is pinned by digest and managed
> separately. See `docs/runbooks/secrets-rotation.md` for NATS rotation procedures.

---

## M1 GHCR auth

GHCR auth is required for `podman auto-update` to check image digests. Store the credential
in the rootless containers config so it persists across reboots:

```bash
podman login ghcr.io
# Enter GitHub username and a PAT with read:packages scope (or use gh auth token).
# Credential is stored at ~/.config/containers/auth.json (rootless).
```

Verify: `podman login --get-login ghcr.io` should print the username.

---

## Fleet STALE_IMAGE badge (digest poll)

The `/fleet` page shows a separate **Image digest** column (distinct from NATS liveness
**Stale**). A host-side poll compares each running Roxabi container's image digest to the
GHCR index digest for its Quadlet `Image=` tag.

| Badge | Meaning |
|---|---|
| **Current** | Running digest matches registry tag |
| **Outdated** | `running_digest != registry_digest` |
| **Unknown** | GHCR auth failed, container not running, or parallel-publish window (#1325) |

State file: `~/.roxabi/factory/state/fleet-digests.json` (hub reads via `factory-data.volume`).

```bash
# Manual refresh on M₁
bash deploy/fleet-digest-poll.sh
systemctl --user start factory-fleet-digest-poll.service   # timer: *:3/5
```

`factory-post-autoupdate` also refreshes digest state after each poll (drift or no-op).

---

## Rollback recipe

Edit the `Image=` line in the affected `.container` file back to the previous semver tag, then
reload and restart:

```bash
# Edit deploy/quadlet/factory-hub.container (and telegram/discord as needed):
#   Image=ghcr.io/roxabi/factory:1.0.0   ← revert to previous known-good tag

systemctl --user daemon-reload
systemctl --user restart factory-hub factory-telegram factory-discord factory-clipool
```

The previous image layer is still present in the local podman store as long as it has not been
pruned, so the restart is immediate with no pull required. Confirm with
`systemctl --user is-active factory-hub factory-telegram factory-discord factory-clipool`.

---

## Schema-floor releases

When `SCHEMA_VERSION_RENDER` (or any contract schema floor constant) is bumped, hub and adapter
container images must be released and deployed **together**. They speak the same wire protocol; a
rolling deploy where the hub is upgraded before the adapters (or vice versa) produces loud ERROR
logs on still-old receivers and may drop events silently.

**Affected units for a render-event schema bump:**

| Unit | Image |
|---|---|
| `factory-hub` | `ghcr.io/roxabi/factory:<tag>` |
| `factory-telegram` | `ghcr.io/roxabi/factory:<tag>` |
| `factory-discord` | `ghcr.io/roxabi/factory:<tag>` |

`factory-clipool` is intentionally **excluded** from the schema-floor restart sequence — it is on
the LLM-driver path (`factory.jobs.claude`), not a `RenderEvent` receiver, and does not participate
in the schema handshake. This omission is deliberate; do not add it back when reading the generic
"M₁ manual pull + restart" pattern above.

All three units share the same image; a single CI push to `staging` produces one `:staging` digest
that all units pull. For semver releases, cut the `factory/<component>/vX.Y.Z` tag once and coordinate
the Quadlet `Image=` pin update across all three `.container` files before `daemon-reload`.

**Release procedure for a schema-floor bump:**

The auto-update timer must NOT fire mid-restart — a 5-minute window between hub and adapter
restarts produces partial-version skew. The procedure is therefore manual: stop the timer,
restart the three units atomically, then re-enable the timer.

1. Merge the schema-bump PR to `staging` — CI publishes `ghcr.io/roxabi/factory:staging`.
2. On M₁, stop the auto-update timer to prevent mid-restart skew:
   ```bash
   systemctl --user stop podman-auto-update.timer
   ```
3. Pull the new image and restart hub + telegram + discord atomically:
   ```bash
   podman pull ghcr.io/roxabi/factory:staging
   systemctl --user restart factory-hub factory-telegram factory-discord
   ```
4. Re-enable the auto-update timer:
   ```bash
   systemctl --user start podman-auto-update.timer
   ```
5. Confirm with `systemctl --user is-active factory-hub factory-telegram factory-discord` and
   `curl -fsS localhost:8443/health`.

For production semver releases, pin all three units to the same `X.Y.Z` tag simultaneously.
Never leave hub and adapters pinned to different semver tags across a schema-floor boundary.

---

## Cross-repo adoption checklist

Steps for a new Roxabi project (voiceCLI, 2ndBrain, imageCLI, llmCLI) to adopt this pattern:

1. Add a production-ready Dockerfile at the repo root following the conventions above: multi-
   stage, pinned base image, non-root UID, and a working `HEALTHCHECK`. The reusable workflow
   automatically forces Docker v2 schema 2 manifest format (`oci-mediatypes=false`), so
   `HEALTHCHECK` is preserved without any extra configuration in the caller workflow.
2. Create `.github/workflows/publish.yml` by copying the **reusable workflow template** above
   (not factory's bake pipeline unless you also maintain a `docker-bake.hcl`). Replace
   `image_name` with `ghcr.io/roxabi/<project>` and `release_please_component` with the
   project's component name. Set the `tags` trigger to `<project>/v*`.
3. Ensure `release-please` is configured in the repo with `tag-separator: '/'` and the correct
   component name matching the value passed to `release_please_component`. Without this, the
   semver tag trigger will not fire.
4. Swap Quadlet or other deploy-manifest `Image=` references from `localhost/<project>:latest`
   (or any locally-built reference) to `ghcr.io/roxabi/<project>:staging`.
5. Push to the `staging` branch, confirm the workflow run completes and the package appears
   under `https://github.com/orgs/Roxabi/packages`, then cut a real release tag to produce the
   first semver image and switch Quadlets to the pinned tag.

---

## Cross-references

- `.github/workflows/publish.yml` — factory caller workflow
- `.github/workflows/omp-base.yml` — path-triggered build+publish for the omp binary carrier image (`ghcr.io/roxabi/factory-omp-base`, immutable version tags); separate from the main bake pipeline — see `deploy/omp-base/README.md`
- `Roxabi/.github/.github/workflows/publish-container.yml@staging` — reusable workflow (upstream)
- `deploy/quadlet/factory-hub.container` — `Image=` reference example
- `deploy/quadlet/factory-telegram.container` — `Image=` reference example
- `deploy/quadlet/factory-discord.container` — `Image=` reference example
- `deploy/quadlet/factory-clipool.container` — `Image=` reference example
- [#920](https://github.com/Roxabi/roxabi-factory/issues/920) — container publishing pattern epic
