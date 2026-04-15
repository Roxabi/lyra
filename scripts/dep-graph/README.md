# dep-graph

GitHub-driven dependency graph generator. Reads `layout.json` + GitHub API, renders an HTML board.

## Usage

```bash
# From ~/projects/lyra/ — single dispatching target (matches monitor/deploy/remote pattern)
make dep-graph           # full rebuild (fetch + build) — default
make dep-graph fetch     # refresh gh.json only
make dep-graph build     # render HTML from existing gh.json
make dep-graph audit     # label-drift check (exit 0 = clean, 1 = drift)
make dep-graph validate  # JSON Schema check on layout
make dep-graph open      # open rendered HTML in browser

# Direct CLI (from scripts/dep-graph/)
python -m dep_graph.cli fetch
python -m dep_graph.cli build
python -m dep_graph.cli audit
python -m dep_graph.cli validate

# Custom paths
python -m dep_graph.cli build \
  --layout ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph.layout.json \
  --cache  ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph.gh.json \
  --out    ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph.html
```

## Common flags

| Flag | Default | Description |
|------|---------|-------------|
| `--layout PATH` | `~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph.layout.json` | Input layout |
| `--cache PATH` | sibling of layout with `.gh.json` suffix | GitHub data cache |
| `--out PATH` | sibling of layout with `.html` or `.gh.json` suffix | Output file |
| `--verbose` | false | Extra logging |
| `--no-validate` | false | Skip schema validation (build only) |

## layout.json schema

See `layout.schema.json` for the full JSON Schema (Draft 7).

Key fields:

```json
{
  "meta": {
    "title": "...",
    "date": "YYYY-MM-DD",
    "repo": "Owner/repo",
    "label_prefix": "graph:",
    "issue": 445
  },
  "lanes": [
    {
      "code": "a",
      "name": "NATS maturity",
      "color": "a",
      "epic": { "issue": 605, "label": "...", "tag": "M1" },
      "order": [568, 572, 576],
      "par_groups": { "g0": [568, 572] },
      "bands": [{ "before": 568, "text": "M0 tail \u2225" }]
    }
  ],
  "standalone": { "order": [604, 646] },
  "overrides": {
    "568": { "size": "XS", "title": "audio_bus probe", "anchor": "m1-gate" }
  },
  "extra_deps": {
    "extra_blocked_by": { "607": [606, 612] },
    "extra_blocking":   { "606": [607] }
  },
  "cross_deps": [
    { "kind": "M3 gate", "text": "B:#609 \u2192 D:#670 \u2014 Langfuse needs containerization" }
  ],
  "title_rules": [
    { "pattern": "^(feat|fix|chore|docs|refactor|test|ci|perf|style)(\\([^)]+\\))?:\\s*", "replacement": "" }
  ]
}
```

## title_rules[]

Sequential regex rules for normalizing GitHub issue titles on cards. Rules are applied in order; the first match that changes the string wins on each rule. `$1` back-references map to `\1`.

Default rules in `layout.json`:

| # | Pattern | Replacement | Effect |
|---|---------|-------------|--------|
| 1 | `^(feat\|fix\|chore\|…)(\([^)]+\))?:\s*` | `""` | Strip conventional-commit prefix |
| 2 | `^M\d+\s*[—-]\s*` | `""` | Strip milestone prefix after conv-commit strip |
| 3 | `\s*[—-]\s+.+$` | `""` | Strip ` — detail suffix` |
| 4 | `\s*\+\s+.+$` | `""` | Strip ` + extra detail` |
| 5 | `\[([^\]]+)\]\s*` | `$1 · ` | Lift bracket scope to front |

Per-issue `overrides.<N>.title` wins over rules (escape hatch for one-off oddities).

Example transformations:

| Raw GitHub title | After rules |
|-----------------|-------------|
| `fix(deploy): M16 — fail fast on git fetch timeout (check returncode)` | `fail fast on git fetch timeout (check returncode)` |
| `chore(ops) [roxabi-ops repo]: scaffold autodeploy — runner skeleton + schema v1 + --dry-run` | `roxabi-ops repo · scaffold autodeploy` |
| `refactor(voice)[S3]: Machine-1 cutover — voicecli_{stt,tts}.conf + make voice-smoke + nkey provisioning` | `S3 · Machine-1 cutover` |

## audit command

```
LABEL DRIFT AUDIT — 2026-04-14

Labeled but not in any lane order[]:
  (none)

In order[] but wrong/missing GH label:
  (none)

graph:defer label vs layout:  (in sync)

graph:standalone label vs standalone.order[]:  (in sync)

RESULT: clean — exit 0
```

Exit 0 = no drift. Exit 1 = drift found (useful as CI check).

## File layout

```
scripts/dep-graph/
  dep_graph/
    __init__.py
    cli.py          argparse entry point (fetch/build/audit/validate)
    fetch.py        GitHub API → gh.json (parameterized)
    build.py        layout.json + gh.json → HTML
    audit.py        label-drift report
    schema.py       JSON schema validation
    titles.py       title_rules engine
  layout.schema.json
  pyproject.toml
  README.md (this file)

~/.roxabi/forge/lyra/visuals/
  lyra-v2-dependency-graph.layout.json   curated input (edit this)
  lyra-v2-dependency-graph.gh.json       fetched cache (auto-generated)
  lyra-v2-dependency-graph.html          rendered output
```

## TODO (deferred)

- `add-issue` subcommand (YAGNI — manual 3-step flow is fine for now)
- Cross-repo dep rendering (Tier 3 / roxabi-dashboard)
- Golden-file tests for build output
- Webhook auto-rebuild on GitHub issue change
- roxabi-dashboard multi-project aggregation shell
