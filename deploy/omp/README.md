# deploy/omp/ — omp (oh-my-pi) runtime config for the factory LiteLLM route

Origin: #1811 (Option D, decided 2026-06-10). Parent: #1490 (pluggable harness
runtime — omp alongside clipool).

## What lives here

| File | Role |
|---|---|
| `models.yml` | omp provider override — points the built-in `litellm` provider at the factory LiteLLM proxy (`:18091`, tailnet) with an explicit model catalog |

## How omp picks this up

omp reads its config from `~/.omp/agent/` by default. The **`PI_CODING_AGENT_DIR`**
env var relocates that whole directory — the worker container mounts a directory
containing this `models.yml` and sets `PI_CODING_AGENT_DIR` to it. No omp source
patch, no port forward.

Override precedence inside omp (verified against oh-my-pi source, 2026-06-10):
`models.yml` provider `baseUrl` > discovered > bundled default
(`packages/coding-agent/src/config/model-registry.ts` — overrides map, priority 1;
default `http://localhost:4000/v1` is a `config?.baseUrl ?? …` fallback in
`packages/ai/src/provider-models/openai-compat.ts`).

## Env surface

| Variable | Consumer | Semantics |
|---|---|---|
| `PI_CODING_AGENT_DIR` | omp (`packages/utils/src/dirs.ts`) | Absolute path of the config dir containing `models.yml`. Mount target for this file in the worker unit. |
| `LITELLM_API_KEY` | omp `litellm` provider | The proxy bearer key. `models.yml` references it **by name** (`apiKey: LITELLM_API_KEY` = env-name-or-literal semantics) — the value enters the container via the #1812 secret mount, never via this repo. ⚠ Fallback: if the env var is **unset**, omp sends the literal string `LITELLM_API_KEY` as the bearer (`resolveApiKeyConfig` — passes omp's non-empty check, rejected by the proxy) → auth failures with a config that looks correct. #1812 must verify injection before relying on proxy auth. |

## Why an explicit `models:` list

omp's built-in catalog discovery resolves the fetch URL from models it already
knows, not from the overrides map — so an override-only entry would still
discover against `localhost:4000` (chicken-and-egg). The explicit list both
dodges that and makes the worker deterministic (no discovery round-trip at
session start, no behaviour change when the proxy catalog moves).

Trade-off: the list **replaces** discovery. Adding a model to the LiteLLM proxy
requires adding its id here. Current catalog = the four models served by the
factory proxy (`/v1/models`, 2026-06-10).

## Validation (gate for #1811)

One-shot re-run of the spike PoC (`artifacts/spikes/1807/omp_rpc_poc.py`) with
`PI_CODING_AGENT_DIR` pointing at a directory containing this `models.yml` —
all 4 probes (text/tool/steer/abort) must PASS against the factory proxy
`:18091` with **no TCP forward**. Result recorded on #1811.

## Boundary

Quadlet integration — S7 secret mount for `LITELLM_API_KEY`, env wiring,
volume-mounting this file into the omp worker unit, ACL identities — is
**#1812** (`OmpWorker`). This directory only proves the config pattern.
