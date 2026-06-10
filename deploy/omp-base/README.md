# deploy/omp-base/ — omp binary carrier image

Origin: #1810 (pin + carrier image). Parent: #1807 (agent-runtime eval, omp ratified 2026-06-10).

## Purpose

`ghcr.io/roxabi/factory-omp-base:<omp-version>` is an **immutable binary-carrier image** that
contains exactly one file: `/opt/omp/omp` (mode 0755), copied from the official
[oh-my-pi](https://github.com/can1357/oh-my-pi) release asset `omp-linux-x64`.

The image is built `FROM scratch` — no OS layer, no shell, no entrypoint. It never runs
directly; consumer images pull the binary out via a `COPY --from=` stage.

The tag is the omp version without the `v` prefix (e.g. `15.10.8`). Tags are **immutable** —
content changes require a version bump (see Immutability guard below).

**Build trigger:** `.github/workflows/omp-base.yml` fires only on path `deploy/omp-base/**`.
It is **never** triggered by the hot `:staging` publish path (`publish.yml`). This keeps the
carrier image lifecycle independent of factory application releases.

## Pin SSoT

The two `ARG` defaults in `deploy/omp-base/Containerfile` are the **sole pin SSoT** for this
image. The workflow reads them — no literal pin lives in the workflow file.

| ARG | Value |
|---|---|
| `OMP_VERSION` | `v15.10.8` |
| `OMP_SHA256` | `b877091c91ebdc8c8d907c4b62681895cd3ae049815858aea7b69ac1d53b7c7b` |

Asset: `omp-linux-x64`, 183,777,408 B — from
[github.com/can1357/oh-my-pi releases](https://github.com/can1357/oh-my-pi/releases).

The SHA256 is verified at build time (`sha256sum -c`) before the binary is admitted into the
image. A mismatch fails the CI job immediately.

## Bump procedure

1. Update the two `ARG` defaults in `Containerfile` — `OMP_VERSION` and `OMP_SHA256`.
2. Open a PR targeting `staging`. The path-trigger on `deploy/omp-base/**` fires
   `.github/workflows/omp-base.yml`.
3. CI `build-smoke` gates:
   - Downloads the new asset.
   - Verifies the SHA256 digest.
   - Runs `omp --version` (smoke stage in the Containerfile).
4. Merge to `staging` → workflow publishes `ghcr.io/roxabi/factory-omp-base:<new-version>`.
5. Open a follow-up commit on **#1812** that bumps the `COPY --from` ref in the OmpWorker
   consumer image to the new tag.

Deep omp_rpc e2e validation on bump is **#1812 CI scope**, not this repo's CI.

## Consumption

Consumer images (e.g. the OmpWorker stage in #1812) copy the binary out of the carrier:

```dockerfile
COPY --from=ghcr.io/roxabi/factory-omp-base:15.10.8 /opt/omp/omp /usr/local/bin/omp
```

**Runtime requirement:** the consumer image MUST be glibc-based. The `omp-linux-x64` binary
links only against `libc`, `libm`, `libpthread`, and `libdl` — it cannot run in a
scratch or musl (Alpine) environment.

## Immutability guard

The publish job checks `docker manifest inspect` post-login before pushing:

- Tag **already exists** → emit `::warning::` + skip push + exit 0.
- Tag **does not exist** → push proceeds normally.

Any content change to the binary or Containerfile MUST be accompanied by a version bump.
There is no `:latest` tag and no floating tag — all references are pinned semver.

## ADR-053 carve-out

The carrier image (`FROM scratch AS carrier`) has no `USER` directive. This is intentional:
the scratch stage never executes — there is no `ld-linux`, no process, no user context.
The non-root runtime requirement (ADR-053) applies to the **consumer's runtime stage**,
which is tracked in **#1812** (`OmpWorker`).
