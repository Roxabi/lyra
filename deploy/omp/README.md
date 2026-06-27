# deploy/omp/ — omp (oh-my-pi) runtime config for the factory LiteLLM route

Origin: #1811 (Option D, decided 2026-06-10). Parent: #1490 (pluggable harness
runtime — omp alongside clipool). Discovery mode: #1923 (reverses #1811 static
list trade-off).

## What lives here

| File | Role |
|---|---|
| `models.yml` | omp provider override — points the built-in `litellm` provider at the factory LiteLLM canonical route (`:18091/v1`, tailnet) with dynamic merged-catalogue discovery |
| `factory-model-policy.yml` | factory-only boot + fallback policy — read by `_model_catalogue.py`, **not** by omp (unknown keys in `models.yml` break provider registration) |

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


## Discovery mode (#1923)

`models.yml` uses omp's built-in `openai-models-list` discovery:

```yaml
discovery:
  type: openai-models-list
```

At boot, omp fetches `GET {baseUrl}/models` (OpenAI-compatible list) and
registers discovered Grok ids. A model added to the LiteLLM proxy becomes
available after **`factory-omp` restart** — no `models.yml` edit, no image
rebuild.

### Why discovery replaces the #1811 static list

#1811 used an explicit `models:` list to dodge a chicken-and-egg: omp's
discovery URL was derived from already-known models, so an override-only entry
still fetched against `localhost:4000`. That trade-off is reversed now that
Roxabi/llmCLI#130 ships a unified merged catalogue at `/v1/models` (TOML
remotes + xAI forwarder + supplements).

### Phases (gateway prerequisites)

| Phase | Gateway prerequisite | omp `baseUrl` | Scope |
|-------|---------------------|---------------|-------|
| **Interim** (done) | Roxabi/llmCLI#129 (`pass_through /xai`) | `:18091/xai/v1` | Discovery + Grok catalogue via xAI route (#1923) |
| **Target** (current) | Roxabi/llmCLI#130 (unified `/v1/models`) | `:18091/v1` | Single canonical base URL; merged catalogue (#1974) |

**Operator procedure when the proxy catalogue changes:** restart `factory-omp`
(`systemctl --user restart factory-omp`). No repo change required for new models
in the merged catalogue.

### Catalogue shape (`GET /v1/models`)

llmCLI publishes one **flat merged list** at `:18091/v1/models` — Grok, local,
and remote ids share the same `data[]` array (not separate per-provider
endpoints). OMP and factory read the same catalogue omp discovers via
`models.yml` `discovery.type: openai-models-list`.

### OMP boot + fallback policy (factory-side, config not code)

omp's subprocess UI discovers models from the same `models.yml`. The factory
`OmpPool` also needs **any** valid catalogue id at `RpcClient.start()` before
per-job `set_model()` runs. That boot pick is **not** a pinned default model —
it comes from `factory-model-policy.yml` (mounted alongside `models.yml`):

```yaml
boot:
  select: first          # first | last | max_lex
  filter:                 # optional
    include_prefix: [grok]
    exclude_contains: [reasoning, multi-agent]
unavailable:              # mid-turn when agent model is invalid
  select: first
  skip_requested: true
  filter:
    exclude_contains: [reasoning]
```

Implementation: `src/factory/adapters/omp/_model_catalogue.py` (`resolve_boot_model`,
`resolve_fallback_model`). No hardcoded model ids in Python. If the catalogue is
empty at boot, the pool fails loud (`ModelCatalogueError`).

Per-agent models (e.g. `aryl_default.model` in the DB) still apply per job via
`RpcBridge.run(model=…)`.

**Related issues:** #1924 (hub `model_cfg` → `RpcClient(model=…)`), #1910
(default model when hub omits `model`).

### Discovery failure behaviour

If the gateway is unreachable, auth fails, or the catalogue is empty, omp logs
the error at boot and session init fails per upstream defaults. This repo does
not add custom retry logic — fix gateway availability or credentials on M₁.

## Validation

- CI: `tests/deploy/test_omp_models_discovery.py` — static YAML invariants +
  mock-gateway catalogue fetch (no M₁ network).
- Live smoke (manual, post-llmCLI#130 deploy on M₁):

  ```bash
  INTEGRATION=1 LITELLM_API_KEY=<key> uv run --frozen pytest \
    tests/deploy/test_omp_models_discovery.py::test_live_gateway_catalogue_has_grok_model -x
  ```

- Historical gate (#1811): spike PoC `artifacts/spikes/1807/omp_rpc_poc.py` with
  `PI_CODING_AGENT_DIR` — superseded for catalogue sync by discovery (#1923).

## Boundary

Quadlet integration — S7 secret mount for `LITELLM_API_KEY`, env wiring,
volume-mounting this file into the omp worker unit, ACL identities — is
**#1812** (`OmpWorker`). This directory only proves the config pattern.