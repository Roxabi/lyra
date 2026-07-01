#!/usr/bin/env bash
# CI gate — the committed Astryx `roxabi` built artifacts must match a fresh
# build of their source (`apps/dashboard/src/astryx-theme/roxabi.theme.ts`).
# `theme:build` is a manual step and no build path regenerates the committed
# `built/` tree, so without this gate an edited source ships stale CSS/tokens
# with green CI. Fix on failure: `bun run --cwd apps/dashboard theme:build` and
# commit the refreshed `built/` artifacts.
# Exit contract (tools/AGENTS.md): 0 = clean, 1 = drift found, 2 = script broke.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dash_dir="${repo_root}/apps/dashboard"
cli="${dash_dir}/node_modules/@astryxdesign/cli/bin/astryx.mjs"
src="src/astryx-theme/roxabi.theme.ts"
committed_dir="${dash_dir}/src/astryx-theme/built"

# The CLI drives via `node` directly (scripts/qg check_requires has no `node`
# token), so guard it here with an actionable message.
if ! command -v node >/dev/null 2>&1; then
  echo "theme_build_drift: 'node' not found on PATH (Astryx CLI needs Node >=22.13)" >&2
  exit 2
fi
if [[ ! -f "${cli}" ]]; then
  echo "theme_build_drift: CLI not found at ${cli} (run 'bun install')" >&2
  exit 2
fi
if [[ ! -d "${committed_dir}" ]]; then
  echo "theme_build_drift: committed built/ dir missing at ${committed_dir}" >&2
  exit 2
fi

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

if ! (cd "${dash_dir}" && node "${cli}" theme build "${src}" --out "${tmp}/roxabi.theme.css") >/dev/null 2>&1; then
  echo "theme_build_drift: 'astryx theme build' failed for ${src}" >&2
  exit 2
fi

# Normalize the volatile header lines each build stamps ('Generated:' timestamp,
# 'Command:' embeds the --out path) before comparing content.
norm() { grep -vE '^[[:space:]]*(/?\*)?[[:space:]]*(Generated|Command):' "$1"; }

status=0
for f in roxabi.theme.css roxabi.js roxabi.d.ts roxabi.variants.d.ts; do
  if [[ ! -f "${committed_dir}/${f}" ]]; then
    echo "theme_build_drift: committed artifact missing: built/${f}" >&2
    status=1
    continue
  fi
  if [[ ! -f "${tmp}/${f}" ]]; then
    echo "theme_build_drift: fresh build did not emit ${f}" >&2
    status=2
    continue
  fi
  if ! diff <(norm "${committed_dir}/${f}") <(norm "${tmp}/${f}") >/dev/null 2>&1; then
    echo "theme_build_drift: built/${f} is stale vs ${src} — run 'bun run --cwd apps/dashboard theme:build' and commit" >&2
    status=1
  fi
done

if [[ "${status}" -eq 0 ]]; then
  echo "theme_build_drift: OK (built/ matches source)"
fi
exit "${status}"
