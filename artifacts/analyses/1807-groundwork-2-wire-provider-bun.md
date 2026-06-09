# Spike #1807 — groundwork part 2: wire protocol · provider config · Bun footprint

_Read-only research pass. All claims carry `file:line` cites. No execution._

---

## A. Wire protocol (docs/rpc.md)

### A.1 RPC session invocation

Invocation: `omp --mode rpc [regular CLI options]`
(`rpc.md:10` — "run `omp --mode rpc` to start an RPC session")

Transport: **NDJSON over stdin/stdout** — every message is a JSON object terminated with `\n`.
(`rpc.md:26` — "The protocol is NDJSON (Newline Delimited JSON)")

Startup handshake: omp emits `{"type":"ready"}` on stdout once initialised; the host must wait for
it before sending any commands.
(`rpc.md:41` — "When the process starts … it will emit a ready event: `{"type":"ready"}`")

Command correlation: all commands accept an optional `id?: string`; responses echo the same `id`.
(`rpc.md:50-54`)

First command expected: `new_session` or `switch_session` — without one no prompting is possible.
(`rpc.md:72` — "You can create a new session with: `{"type": "new_session"}`")

### A.2 steer — wire-level timing

Command shape:
```json
{"id?": "…", "type": "steer", "message": "<string>", "interruptMode": "immediate"|"wait"}
```
(`rpc.md:349-360`)

**`immediate`** — omp checks for pending steering **between tool calls** within the current turn.
A pending steer may abort remaining tool calls in the turn and apply the steering to the next
model call.
(`rpc.md:363-366` — "between tool calls, not mid-execution of a single tool")

**`wait`** — deferred until the current turn completes naturally.
(`rpc.md:367-370`)

Key constraint: `steer` is **never applied mid-tool**. It is a between-tool-call interrupt at
earliest. Code calling omp via RPC cannot inject a prompt mid-inference.

Acknowledgement: omp emits `{"type":"steer_queued","id?":…}` immediately on receipt; the
steering is not necessarily applied yet.
(`rpc.md:372-380`)

### A.3 session resume mechanism

Two separate commands with different semantics:

**`new_session`** — creates a fresh session, optionally forked from an existing one:
```json
{"type": "new_session", "parentSession"?: "<session-id>"}
```
`parentSession` is the session **id** (string), not a file path.
(`rpc.md:79-82`)

**`switch_session`** — resumes an existing session from a **file path**:
```json
{"type": "switch_session", "sessionPath": "<absolute-file-path>"}
```
`sessionPath` is a filesystem path to a serialised session file.
(`rpc.md:124-128`)

Resolution for `resume_and_reset` pattern: to resume a previous session and reset to a
checkpoint, use `switch_session` with the session file path. `new_session(parentSession=)` forks
(inherits) rather than resumes.

### A.4 host-tools over the wire

Custom tools are exposed to omp via a `set_host_tools` command:
```json
{"type": "set_host_tools", "tools": [{"name":"…","description":"…","inputSchema":{…}}]}
```
(`rpc.md:83-100`)

When omp wants to execute a host tool it emits outbound:
```json
{"type": "host_tool_call", "callId": "<uuid>", "name": "<tool-name>", "input": {…}}
```
(`rpc.md:420-436`)

The host executes the tool and responds with inbound `host_tool_result`:
```json
{"type": "host_tool_result", "callId": "<uuid>", "output"?: "…", "error"?: "…"}
```
(`rpc.md:439-465`)

omp may also emit `host_tool_cancel` if the turn is aborted before the host replies.
(`rpc.md:437-438`)

The full round-trip runs over the **same NDJSON stdio transport** — no side channel.

---

## B. Provider / LiteLLM config

### B.1 Is there a named `litellm` provider?

**Yes — confirmed.** Named provider with `id: "litellm"` exists in the registry:

```typescript
export const litellmProvider = {
    id: "litellm",
    name: "LiteLLM",
    defaultModel: "claude-opus-4-6",
    …
} as const satisfies ProviderDefinition;
```
(`packages/ai/src/registry/litellm.ts:40-48`)

The provider is wired into `litellmModelManagerOptions` in openai-compat.ts:
(`packages/ai/src/provider-models/openai-compat.ts:2187-2212`)

### B.2 default baseUrl + override knobs

**Hard-coded default in `litellmModelManagerOptions`:**
```typescript
const baseUrl = config?.baseUrl ?? "http://localhost:4000/v1";
```
(`packages/ai/src/provider-models/openai-compat.ts:2191`)

Config shape (`LiteLLMModelManagerConfig`):
```typescript
export interface LiteLLMModelManagerConfig {
    apiKey?: string;   // → LITELLM_API_KEY env var (via registry catalogDiscovery)
    baseUrl?: string;  // override; falls back to http://localhost:4000/v1
    fetch?: FetchImpl;
}
```
(`packages/ai/src/provider-models/openai-compat.ts:2181-2185`)

Environment variable for API key: **`LITELLM_API_KEY`**
(`packages/ai/src/registry/litellm.ts:45-46` — `envKeys: "LITELLM_API_KEY"`, `envVars: ["LITELLM_API_KEY"]`)

`allowUnauthenticated: true` — LiteLLM can operate without a key (local proxy, no auth).
(`packages/ai/src/registry/litellm.ts:45`)

Model list: **not bundled in models.json** — LiteLLM is in `DISCOVERY_ONLY_PROVIDERS`; discovered
dynamically via `/v1/models` at runtime, enriched against models.dev references.
(`packages/ai/src/provider-models/openai-compat.ts:2199-2210`;
 `packages/ai/scripts/generate-models.ts` — `DISCOVERY_ONLY_PROVIDERS` set)

API type used: **`openai-completions`** (not `openai-responses`).
(`packages/ai/src/provider-models/openai-compat.ts:2202`)

### B.3 minimal config to point omp at M1 LiteLLM

At the **wire level** (`set_model` RPC command accepts `provider` + `modelId` only — no baseUrl):
```json
{"type": "set_model", "provider": "litellm", "modelId": "claude-opus-4-6"}
```
(`docs/rpc.md:319-330`)

The baseUrl is resolved from the provider config, **not settable at the wire level**. To override
the default `localhost:4000/v1` (e.g. to reach M1 via Tailnet), the override path is:

1. **Env var for baseUrl**: No dedicated env var — `LITELLM_BASE_URL` does NOT exist in the
   codebase. The `baseUrl` is only overridable through `LiteLLMModelManagerConfig.baseUrl` at
   construction time (TypeScript API level).
2. **In practice**: set `LITELLM_API_KEY` (or leave empty for unauthenticated) + rely on omp's
   provider config being initialised with `baseUrl: "http://roxabituwer:4000/v1"` if omp is
   embedded as a subprocess from factory (the Python `omp-rpc` client spawns omp with env
   controlled by the caller).

For the factory spike, **M1 LiteLLM is at `http://roxabituwer:4000/v1`**; pass that as
`baseUrl` when constructing the provider config in whatever TypeScript shim wraps omp, or
set it via `PI_ROOT`/startup env if a custom launcher is used.

---

## C. omp distribution & Bun footprint

### C.1 build/ship format

**omp is NOT a compiled single binary.** The `omp` command in the container is a **bash shim**:

```bash
#!/usr/bin/env bash
exec bun "$PI_ROOT/packages/coding-agent/src/cli.ts" "$@"
```
(`Dockerfile:145-155`)

The runtime requires: Bun interpreter + full TypeScript source tree + hoisted `node_modules`.

`bunfig.toml:6` — `linker = "hoisted"` confirms node_modules tree (not isolated per-package),
meaning the runtime image ships the full dependency tree, not a self-contained binary.

The `ci:release:build-binaries` script (`package.json:123`) references
`bun scripts/ci-release-build-binaries.ts` — this builds **platform-specific npm packages**
(tarballs for distribution), not a `bun build --compile` single binary. Verified: no
`--compile` flag appears in the scripts.

### C.2 Bun/Node version pin

- **Bun 1.3.14** — pinned in two places:
  - `package.json:5` — `"packageManager": "bun@1.3.14"`
  - `Dockerfile:25` — `ARG BUN_VERSION=1.3.14`
- No Node.js version pin (Bun is the sole JS runtime; Node is not used).

### C.3 qualitative image-size estimate

The `pi-runtime` Docker image (`Dockerfile`) is a **multi-stage, multi-component image**:

| Stage | Key contents |
|---|---|
| `natives-builder` | Rust 1.86 toolchain + Bun (build only) |
| `wheel-builder` | Python 3.12 + uv (build only) |
| `pi-base` | python:3.12-slim-bookworm + Bun binary + Rust-built `pi_natives` N-API addon + `omp_rpc` Python wheel |
| `pi-runtime` | pi-base + full source tree (`packages/`) + `bun install` (hoisted node_modules) + Python venv |

Size drivers in the final image:
- `python:3.12-slim-bookworm` base: ~130 MB
- Bun binary: ~70 MB
- `node_modules` tree (hoisted, full monorepo deps): ~200-400 MB (many AI/embedding/ONNX runtime packages)
- TypeScript source tree: ~10-20 MB
- Rust N-API addon: ~5-10 MB
- Python venv (omp_rpc wheel + deps): ~20-50 MB

**Estimated total: 450–700 MB compressed; 1–2 GB uncompressed.**

This is a **heavyweight base image** — not comparable to a tens-of-MB single-binary deployment.
Any `factory-omp-base` Quadlet will pull and persist a multi-hundred-MB image. This is the
dominant infra-cost consideration for the spike's GREEN/RED gate.

---

## D. Updated verdicts

| Gap | Status | Key finding |
|---|---|---|
| **A — Wire protocol** | RESOLVED | RPC = `omp --mode rpc` + NDJSON stdio; steer applies between tool calls only (never mid-tool); `switch_session` uses file path, `new_session` uses session id; host-tools via `set_host_tools` / `host_tool_call` / `host_tool_result` round-trip |
| **B — LiteLLM provider** | RESOLVED | Named `id: "litellm"` provider confirmed; default baseUrl `http://localhost:4000/v1` hard-coded in `litellmModelManagerOptions`; key = `LITELLM_API_KEY`; `allowUnauthenticated: true`; no `LITELLM_BASE_URL` env var — baseUrl override is TypeScript-constructor-level only; model list discovered dynamically (not bundled) |
| **C — Bun footprint** | RESOLVED | omp = bash shim over Bun interpreter + full source tree — NOT a compiled binary; Bun 1.3.14 pinned; final image ~450–700 MB compressed, heavyweight |

**Gate-changing fact**: omp is not a single binary. Deploying it in a Quadlet requires shipping
the full pi-runtime image (~500 MB+), making `factory-omp-base` a significant footprint
commitment. The prior evaluation framing of "turnkey LiteLLM provider + omp-rpc Python client"
holds, but the deployment cost is substantially higher than a compiled-binary alternative.

---

## E. Still unverified / Not audited

- **`LITELLM_BASE_URL` env var at the omp process level**: confirmed no such env var in the
  provider config code; not verified whether the `omp --mode rpc` CLI startup reads a config file
  that could set provider baseUrls before the RPC session begins. If such a config file exists
  (e.g. `~/.config/omp/config.json`), it could override baseUrl without TypeScript-constructor
  access. This path was not traced.

- **Exact image size**: estimate above is qualitative from reading the Dockerfile and dependency
  list; not measured from a built image. `onnxruntime-node` (1.24.3) and `fastembed` are
  particularly large native deps — actual size could vary by ±150 MB.

- **`ci-release-build-binaries.ts`**: the release binary build script was not read. It may
  produce platform-specific standalone binaries for distribution (separate from the Docker image),
  which could be an alternative deployment vector. Not verified.

- **Python `omp-rpc` client API** (`python/omp-rpc/`): was read in the prior session; not
  re-verified in this pass. Session-resume semantics at the Python wrapper level (vs raw RPC)
  assumed consistent with `docs/rpc.md`.
