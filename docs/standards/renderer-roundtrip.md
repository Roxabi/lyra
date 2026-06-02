---
title: Renderer→Consumer Roundtrip CI Pattern
description: When and how to add CI validation that a config renderer's output is accepted by the downstream binary before merge.
---

# Renderer→Consumer Roundtrip CI Pattern

> Status: LIVING
> Scope: `deploy/`, `tools/` — any tool that generates a config file consumed by an external binary
> Enforced by: `.github/workflows/renderer-roundtrip.yml` + `tools/check_renderer_roundtrip.sh`

---

## Pattern

A *renderer* is any tool or static file that produces output consumed by an external binary (nats-server, systemd, openssl, podman). The downstream binary is the authoritative parser — it decides at runtime whether the output is valid. The problem: validation at write-time is often weaker than validation at read-time. A rendered config may be accepted on first deploy yet rejected silently at the next restart, sometimes days later.

Two prod incidents drove this pattern:

- **#1083** — a `#` character in a Quadlet `Volume=` line was consumed without error at deploy but caused podman to misparse the unit file at next container start.
- **#1089** — `factory-acl genkeys` emitted 62-char nkeys instead of the required 56; nats-server accepted the auth.conf at initial load but rejected the keys on restart.

The fix: every renderer must be exercised in CI by invoking the actual downstream binary in parse/check mode on its output, with exit-code and structural-invariant assertions, before the PR is merged.

Shape: **render → consumer parse → invariant assertions**.

---

## When to apply

Apply when:

- A tool (Python script, shell script, CLI subcommand) writes a file that an external binary will parse.
- A static file in `deploy/` is shipped to be consumed by `nats-server`, `podman`/systemd Quadlet, `systemd`, or `openssl`.
- An existing renderer is modified in a way that changes the output format or adds new fields.

Skip when:

- The file is an internal Python data structure (e.g. a pickled object, a JSON blob read only by Lyra itself).
- The consumer is Lyra code, not an external binary — those cases belong in `tests/` as integration tests.
- The file is documentation or a template that is never loaded by a binary directly.

---

## Required shape

Every roundtrip test must implement all three steps. Partial coverage (render only, or consumer parse only) does not satisfy this standard.

**Render step**

The renderer must write its output to a CI-controllable directory. Use `$RUNNER_TEMP` in CI or an env-overridable path in the helper script — never hardcode `/tmp/`. Example: `deploy/nats/gen-certs.sh` accepts `CERT_DIR=$tmpdir`, making it safe to invoke in parallel CI jobs without path collisions. New renderers must accept an equivalent override.

**Consumer parse step**

Invoke the downstream binary in its check/dry-run mode on the rendered output. No live network binding is required or permitted. Canonical invocations:

```bash
nats-server -t -c "$conf_file"          # syntax + config parse only
systemd-analyze verify "$unit_file"     # unit file semantic check
openssl verify -CAfile ca.crt leaf.crt  # certificate chain validation
podman quadlet --dryrun "$unit_file"    # Quadlet parse without starting
```

The binary must be the actual downstream binary, not a reimplementation or mock.

**Invariant assertions**

Consumer exit code 0 is necessary but not sufficient. Add explicit assertions for any structural semantic invariant the binary does not enforce at parse time. Format failures as `expected X, got Y` — not a generic error string. Examples:

- nkeys must be exactly 56 chars (nats-server parses them but does not validate length at config load).
- Every certificate in `certs/` must chain to the CA in `ca.crt`.
- No embedded newline inside a quoted value in the rendered conf.

---

## Worked example

`factory-acl genkeys` renders an `auth.conf` block containing nkeys and seeds. Bug #1089: the keygen utility emitted 62-char public keys. nats-server accepted the auth.conf without error on the initial load but failed to authenticate connections at restart because the keys did not match the internal nkeys format.

The roundtrip test covers this renderer:

```bash
bash tools/check_renderer_roundtrip.sh factory-acl "$(mktemp -d)"
```

The helper generates a fresh keyset into the supplied tmpdir, renders an auth.conf, runs `nats-server -t -c` on it, then asserts three invariants:

1. **Length** — every nkey in the rendered file is exactly 56 characters.
2. **Round-trip** — `pubkey(seed)` recomputed from the seed file equals the nkey stored in auth.conf.
3. **No embedded newline** — no quoted string value in the conf contains a literal newline character.

`tools/check_renderer_roundtrip.sh` is the canonical dispatcher for all renderers. When adding a new renderer, add a subcommand (case branch) to that script rather than creating a new standalone script. This keeps all roundtrip invocations discoverable in one place and ensures the CI workflow (`renderer-roundtrip.yml`) picks them up automatically.

---

## Failure modes

**Surface a useful diff, not a generic exit 1.**

Error messages must name the violation, the location, and the observed value. Examples of compliant error output:

```
nkey expected length 56, got 62 at user 'hub'
cert chain: leaf.crt does not verify against ca.crt (openssl exit 2)
volume line contains '#': 'Volume=/data/lyra#backup:/backup' in lyra-hub.container
```

Bad: `Error: validation failed` — this is not actionable. The developer must be able to fix the issue without re-running locally.

**Don't swallow consumer warnings as errors — but don't ignore real warnings either.**

`systemd-analyze verify` emits warnings for undeclared dependency targets (e.g. `network.target` not present in the test environment). These are expected and should not cause the test to fail — only a non-zero exit code from the binary is a hard failure.

Conversely, if `nats-server` emits a stderr warning about a deprecated directive, treat it as a failure signal: the warning indicates the binary did not fully parse the config as intended. The threshold rule: any warning that indicates the consumer does not understand a directive → fail; any warning about the test environment lacking runtime dependencies → tolerate.

---

## Reviewer checklist

> **Does this PR add or modify a config renderer? If yes:**
> - (a) check that `.github/workflows/renderer-roundtrip.yml` has a job invoking `tools/check_renderer_roundtrip.sh <subcommand>` for the renderer
> - (b) verify `tools/check_renderer_roundtrip.sh` has a `case` branch implementing the subcommand

- Is the consumer the actual downstream binary (nats-server, systemd, openssl, podman), not a mock or re-implementation?
- Does the test include a negative case — a known-bad input that causes the test to fail? A test that cannot fail on bad input is not a roundtrip test.
- Does the helper script invocation follow the canonical shape in `tools/check_renderer_roundtrip.sh` (subcommand + output dir), not a bespoke script?
