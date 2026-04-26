# Container Publishing Pattern

## Overview

Lyra containers are built and published to GHCR via a reusable GitHub Actions workflow shared
across all Roxabi projects. The registry convention is `ghcr.io/roxabi/<project>`. Two triggers
drive publishing: a push to the `staging` branch produces a `:staging` floating tag for
pre-release validation on M₁; a release-please tag of the form `<component>/vX.Y.Z` on the
`main` branch produces `:X.Y.Z`, `:X` (major alias), and `:latest`, all managed via
`docker/metadata-action@v5`. The reusable workflow lives at
`Roxabi/.github/.github/workflows/publish-container.yml@v1`; each project supplies a thin
caller workflow that feeds project-specific inputs.

---

## Dockerfile conventions

- **Multi-stage build** — at minimum a build stage and a runtime stage; never ship build tools in
  the final image.
- **Pinned base image** — use an explicit minor version (e.g. `python:3.12.10-slim`); digest
  pinning is preferred for the runtime stage in high-security contexts.
- **Pinned package layers** — pin `uv` by version in the build stage; `apt-get` layers must use
  `--no-install-recommends` and clean lists in the same `RUN` step.
- **Explicit non-root UID** — create a dedicated system user with a fixed numeric UID. Lyra uses
  UID/GID 1500 (`lyra`). Never run as root or rely on the default `nobody` UID.
- **HEALTHCHECK** — must exit 0 on healthy, non-zero on unhealthy. Lyra uses
  `HEALTHCHECK CMD lyra config validate`. The command must be available in the final stage.

  > **Note:** `HEALTHCHECK` requires Docker manifest format (v2 schema 2). OCI image manifests
  > silently drop this instruction. The reusable workflow sets `oci-mediatypes=false` on the
  > `docker/build-push-action` step to force Docker v2 schema 2, so `HEALTHCHECK` is preserved
  > in the published image. No action needed in the Dockerfile or caller workflow.
- **OCI labels** — do not set `org.opencontainers.image.*` labels in the Dockerfile. They are
  injected at build time by `docker/metadata-action@v5` in the reusable workflow, ensuring labels
  always match the actual pushed tag and commit SHA.

---

## Caller workflow template

The reusable workflow accepts four inputs:

| Input | Required | Default | Description |
|---|---|---|---|
| `image_name` | yes | — | Full registry path, e.g. `ghcr.io/roxabi/lyra` |
| `release_please_component` | yes | — | Component name as used in the release-please tag, e.g. `lyra` |
| `dockerfile_path` | no | `./Dockerfile` | Path to the Dockerfile relative to the build context |
| `build_context` | no | `.` | Docker build context path |

Lyra caller (`.github/workflows/publish.yml`):

```yaml
name: publish
on:
  push:
    branches: [staging]
    tags: ['lyra/v*']
permissions:
  contents: read
  packages: write
jobs:
  publish:
    uses: Roxabi/.github/.github/workflows/publish-container.yml@v1
    secrets: inherit
    with:
      image_name: ghcr.io/roxabi/lyra
      release_please_component: lyra
      # Manifest format (oci-mediatypes=false) is handled by the reusable workflow.
      # No extra inputs are needed to preserve HEALTHCHECK.
```

Callers MUST pin `@v1`, never `@main`. The `main` branch of `Roxabi/.github` may receive
breaking changes between major versions. The `v1` branch advances forward only for backward-
compatible changes.

---

## Quadlet `Image=` convention

Two rules govern how Quadlet units reference the published image:

**Rule A — production (post-first-release):** pin to the immutable semver tag produced by
release-please. This ensures a daemon-reload never silently pulls a different layer.

```ini
# Before (floating staging tag):
Image=ghcr.io/roxabi/lyra:staging

# After first release cut (semver pin):
Image=ghcr.io/roxabi/lyra:1.0.0
```

**Rule B — pre-release / staging validation:** use `:staging` so that each push to the
`staging` branch is picked up on the next pull without a Quadlet edit.

Switching between the two is a one-line edit to the `.container` file followed by
`systemctl --user daemon-reload`.

---

## M1 pull + restart runbook

After a new image is pushed (either `:staging` or a semver tag), pull and restart on M₁
(`roxabituwer`, rootless podman 5.x):

```bash
podman pull ghcr.io/roxabi/lyra:staging
systemctl --user daemon-reload
systemctl --user restart lyra-hub lyra-telegram lyra-discord
```

Verify all three units are healthy:

```bash
systemctl --user is-active lyra-hub lyra-telegram lyra-discord
curl -fsS localhost:8443/health
```

`is-active` prints `active` for each unit on success. The health endpoint is served by
`lyra-hub` on `127.0.0.1:8443` (published via `PublishPort` in the Quadlet unit).

---

## M1 auth for private images (future)

Public GHCR images require no authentication; `podman pull` works without credentials as long
as the image visibility is set to public in the GitHub package settings.

If a project is ever published as a private package, authenticate before pulling. Store the
credential in the rootless containers config so it persists across reboots:

```bash
podman login ghcr.io
# Enter GitHub username and a PAT with read:packages scope.
# Credential is stored at ~/.config/containers/auth.json (rootless)
# or ~/.docker/config.json if podman falls back to the Docker credential store.
```

This is a placeholder — no Roxabi project currently requires GHCR authentication to pull.

---

## Rollback recipe

Edit the `Image=` line in the affected `.container` file back to the previous semver tag, then
reload and restart:

```bash
# Edit deploy/quadlet/lyra-hub.container (and telegram/discord as needed):
#   Image=ghcr.io/roxabi/lyra:1.0.0   ← revert to previous known-good tag

systemctl --user daemon-reload
systemctl --user restart lyra-hub lyra-telegram lyra-discord
```

The previous image layer is still present in the local podman store as long as it has not been
pruned, so the restart is immediate with no pull required. Confirm with
`systemctl --user is-active lyra-hub lyra-telegram lyra-discord`.

---

## Cross-repo adoption checklist

Steps for a new Roxabi project (voiceCLI, 2ndBrain, imageCLI, llmCLI) to adopt this pattern:

1. Add a production-ready `Dockerfile` at the repo root following the conventions above: multi-
   stage, pinned base image, non-root UID, and a working `HEALTHCHECK`. The reusable workflow
   automatically forces Docker v2 schema 2 manifest format (`oci-mediatypes=false`), so
   `HEALTHCHECK` is preserved without any extra configuration in the caller workflow.
2. Create `.github/workflows/publish.yml` by copying the caller template above. Replace
   `image_name` with `ghcr.io/roxabi/<project>` and `release_please_component` with the
   project's component name. Update the `tags` trigger from `lyra/v*` to `<project>/v*`.
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

- `.github/workflows/publish.yml` — lyra caller workflow
- `Roxabi/.github/.github/workflows/publish-container.yml@v1` — reusable workflow (upstream)
- `deploy/quadlet/lyra-hub.container` — `Image=` reference example
- `deploy/quadlet/lyra-telegram.container` — `Image=` reference example
- `deploy/quadlet/lyra-discord.container` — `Image=` reference example
- [#920](https://github.com/Roxabi/lyra/issues/920) — container publishing pattern epic
