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
python -m dep_graph.cli migrate <layout-path>

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

## CLI subcommands

| Subcommand | Make target | Description |
|------------|-------------|-------------|
| `fetch` | `make dep-graph fetch` | Refresh GitHub cache (gh.json) |
| `build` | `make dep-graph build` | Render HTML from layout + cache |
| `audit` | `make dep-graph audit` | Label-drift check |
| `validate` | `make dep-graph validate` | JSON Schema check on layout |
| `migrate` | — (not wired to Make) | Migrate layout to multi-repo format |

## layout.json schema

See `layout.schema.json` for the full JSON Schema (Draft 7).

### Matrix config (optional): `milestones` + `column_groups`

The v5 grid view renders a milestone × column_group matrix. Defaults live in `v5/data/model.py` (`MILESTONES`, `COLUMN_GROUPS`) and cover the current lyra roadmap (M0–M10 + Final, 15 columns a1–o). Override either list from `layout.json` to drive the matrix from config — useful when lane/milestone shape drifts on GitHub and you'd rather bump the layout than edit Python.

Both keys are optional and independent (set one, the other, or both). When absent, the module defaults apply.

```json
{
  "milestones": [
    {"label": "M0  NATS hardening", "code": "M0", "short": "NATS hardening"},
    {"label": "M1  Containerize",   "code": "M1", "short": "Containerize"}
  ],
  "column_groups": [
    {"label": "NATS",      "tone": "a1", "lane_codes": ["a1", "a2", "a3"]},
    {"label": "CONTAINER", "tone": "b",  "lane_codes": ["b"]}
  ]
}
```

- `milestones[].label` must match the GitHub milestone title with em-dashes stripped to double-space (same rule as the hardcoded defaults). `code` is the short code (`M0`), `short` is the row-header display name.
- `column_groups[].lane_codes` lists the 1+ lane codes bundled under one column. `tone` is the CSS tone key used for the header color.
- Visible issues lacking a milestone or lane land in sentinel `NO_MS` row / `NO_LANE` column — auto-hidden when empty, rendered when non-empty.

### Other key fields

```json
{
  "meta": {
    "title": "...",
    "date": "YYYY-MM-DD",
    "repos": ["Roxabi/lyra"],
    "label_prefix": "graph:"
  },
  "lanes": [
    {
      "code": "a",
      "name": "NATS maturity",
      "color": "a",
      "epic": { "issue": 605, "label": "...", "tag": "M1" },
      "order": [
        {"repo": "Roxabi/lyra", "issue": 568},
        {"repo": "Roxabi/lyra", "issue": 572}
      ],
      "par_groups": { "g0": [{"repo": "Roxabi/lyra", "issue": 568}, {"repo": "Roxabi/lyra", "issue": 572}] },
      "bands": [{ "before": {"repo": "Roxabi/lyra", "issue": 568}, "text": "M0 tail \u2225" }]
    }
  ],
  "standalone": { "order": [{"repo": "Roxabi/lyra", "issue": 604}] },
  "overrides": {
    "Roxabi/lyra#568": { "size": "XS", "title": "audio_bus probe", "anchor": "m1-gate" }
  },
  "extra_deps": {
    "extra_blocked_by": { "Roxabi/lyra#607": ["Roxabi/lyra#606", "Roxabi/lyra#612"] },
    "extra_blocking":   { "Roxabi/lyra#606": ["Roxabi/lyra#607"] }
  },
  "cross_deps": [
    { "kind": "M3 gate", "text": "B:#609 \u2192 D:#670 \u2014 Langfuse needs containerization" }
  ],
  "title_rules": [
    { "pattern": "^(feat|fix|chore|docs|refactor|test|ci|perf|style)(\\([^)]+\\))?:\\s*", "replacement": "" }
  ]
}
```

**Breaking change (multi-repo):** `meta.repo` (singular string) is replaced by `meta.repos[]` (array). Issue refs throughout (`order[]`, `par_groups`, `bands[].before`, `standalone.order[]`) are now `{"repo": "Owner/repo", "issue": N}` objects instead of bare integers. Override and `extra_deps` keys use `"Owner/repo#N"` instead of bare `"N"`. Run `python -m dep_graph.cli migrate <layout-path>` to upgrade.

## Multi-repo support

`meta.repos[]` replaces `meta.repo` (singular). List every GitHub repo whose issues appear in the graph.

Complete layout example with 2 repos:

```json
{
  "meta": {
    "title": "Lyra v2",
    "date": "2026-04-15",
    "repos": ["Roxabi/lyra", "Roxabi/roxabi-vault"],
    "label_prefix": "graph:"
  },
  "lanes": [{
    "code": "i",
    "name": "Vault ingest",
    "color": "i",
    "epic": {"issue": 704, "label": "...", "tag": "I"},
    "order": [
      {"repo": "Roxabi/lyra",         "issue": 703},
      {"repo": "Roxabi/roxabi-vault", "issue": 24}
    ],
    "par_groups": {},
    "bands": []
  }],
  "standalone": {"order": []},
  "overrides": {
    "Roxabi/roxabi-vault#24": {"title": "NATS subscriber"}
  },
  "extra_deps": {"extra_blocked_by": {}, "extra_blocking": {}},
  "cross_deps": [],
  "title_rules": []
}
```

Key behaviors:

- **Primary repo** = `meta.repos[0]`. Cards from any other repo get a repo badge in the rendered HTML.
- **Cross-repo edges** — `blocked_by` / `blocking` relationships come from GitHub's native issue dependency API. No custom format needed: just link issues in GitHub via the issue dependency UI, or via `gh api`:
  ```bash
  gh api repos/Roxabi/lyra/issues/703/dependencies/blocked_by \
    --method POST -F issue_id=<vault-issue-node-id>
  ```
  The fetcher calls `/repos/OWNER/REPO/issues/NUM/dependencies/blocked_by` and `/dependencies/blocking` for every issue in the merged pool and stores the results in `gh.json`.
- **Not-found placeholder (C9)** — if an `IssueRef` in the layout is missing from `gh.json` (e.g., the issue doesn't exist or wasn't fetched), a red-dashed placeholder card renders on the board and a warning is emitted to stderr.

## Migration

Migrate a single-repo layout (bare int refs, `meta.repo`) to the new multi-repo format.

```bash
python -m dep_graph.cli migrate <layout-path>
# Example:
python -m dep_graph.cli migrate \
  ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph.layout.json
```

Note: `make dep-graph migrate` is **not wired** — use the direct CLI form above.

Behavior:

- Writes `<layout-path>.new` — the original file is **never mutated**.
- Review the diff, rename `.new` into place, and optionally keep the original as backup.
- **Idempotent** — if the layout already validates against the new schema (already migrated), exits 0 with `"Already migrated."` and does not write `.new`.
- **Partial migration** — a layout with `meta.repos[]` already set but still containing bare-int refs is detected (schema validation fails) and completed.
- **Rollback** — `.new` is the rollback artifact. Keep the original until you're confident in the result.

What the migrator transforms:

| Location | Old format | New format |
|----------|-----------|-----------|
| `meta.repo` | `"Roxabi/lyra"` | `meta.repos: ["Roxabi/lyra"]` |
| `lanes[].order[]` | `568` | `{"repo": "Roxabi/lyra", "issue": 568}` |
| `lanes[].par_groups.*[]` | `568` | `{"repo": "Roxabi/lyra", "issue": 568}` |
| `lanes[].bands[].before` | `568` | `{"repo": "Roxabi/lyra", "issue": 568}` |
| `standalone.order[]` | `604` | `{"repo": "Roxabi/lyra", "issue": 604}` |
| `overrides` keys | `"568"` | `"Roxabi/lyra#568"` |
| `extra_deps.*` keys/values | `"607": [606]` | `"Roxabi/lyra#607": ["Roxabi/lyra#606"]` |

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

Per-issue `overrides.<Owner/repo#N>.title` wins over rules (escape hatch for one-off oddities).

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
    cli.py          argparse entry point (fetch/build/audit/validate/migrate)
    fetch.py        GitHub API → gh.json (multi-repo, parallel)
    build.py        layout.json + gh.json → HTML
    audit.py        label-drift report
    schema.py       JSON schema validation
    titles.py       title_rules engine
    migrate.py      migrate layout to multi-repo format
    keys.py         gh.json key helpers (owner/repo#N parse/format)
  layout.schema.json
  pyproject.toml
  README.md (this file)

~/.roxabi/forge/lyra/visuals/
  lyra-v2-dependency-graph.layout.json   curated input (edit this)
  lyra-v2-dependency-graph.gh.json       fetched cache (auto-generated)
  lyra-v2-dependency-graph.html          rendered output
```

## Risks & caveats

- **GitHub public preview API** — `/repos/OWNER/REPO/issues/NUM/dependencies/{blocked_by,blocking}` is in public preview and may change shape or access scope without notice. The fetcher asserts the expected payload shape (`{number, repository.full_name}` or `{repo, issue}`) on the first non-empty response and raises with the observed payload on mismatch. Check `dep_graph/fetch.py:_check_dep_shape` if this breaks.
- **`gh` auth required for all repos** — `gh auth status` must be authenticated for every repo in `meta.repos[]`. Private repos require a token scope that covers them. A foreign repo you can't read will cause `fetch` to fail fast.
- **Cross-repo edges only render for repos in the pool** — if `lyra#703` is `blocked_by` `vault#24` but `Roxabi/roxabi-vault` is not in `meta.repos[]`, the edge won't appear in the graph. Add the missing repo to `meta.repos[]` to surface it.
- **Schema evolution** — if `IssueRef` gains fields later (e.g., `branch`), a second migration pass will be needed. The current shape `{"repo": str, "issue": int}` is the intended stable natural key.

## v5 — dual-mode builder (table + graph)

**Default output**: `~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph-v5.1.html`.

`make dep-graph` (no sub-action) runs `fetch` + the v5 build. The v5 package is **independent** of v3.1 / v4.8 — it loads `layout.json` + `gh.json` directly, derives status/depth itself, and emits a single HTML with a `[Graph] | [Table]` mode toggle.

Layout:

```
scripts/dep-graph/v5/
├── build.py         · compose.py      · __init__.py
├── data/            model · load · derive · layout_graph (DAG math)
├── views/           grid (v3.1 swim-lane) · graph (dots + pill labels)
├── components/      card · header · toolbar · toggle
├── assets/          tokens · base · toggle · card · grid · graph .css
│                    hover · app .js
└── tests/           conftest + 9 suites (255 tests)
```

Run modes:

| Command | Effect |
|---|---|
| `make dep-graph` | fetch + v5 build → v5.1 HTML (default) |
| `make dep-graph fetch` | refresh `gh.json` only |
| `make dep-graph build` | v5 build (no fetch) |
| `make dep-graph legacy` | old `dep_graph.cli` build → `lyra-v2-dependency-graph.html` |
| `make dep-graph open` | open the v5.1 HTML |
| `make dep-graph audit` · `validate` · `migrate` | schema ops |

## TODO (deferred)

- `add-issue` subcommand (YAGNI — manual 3-step flow is fine for now)
- Golden-file tests for build output
- Webhook auto-rebuild on GitHub issue change
- roxabi-dashboard multi-project aggregation shell
