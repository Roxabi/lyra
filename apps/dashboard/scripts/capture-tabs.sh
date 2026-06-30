#!/usr/bin/env bash
# Capture all dashboard tabs — usage: ./scripts/capture-tabs.sh [before|after]
set -euo pipefail
LABEL="${1:-before}"
OUT="$(cd "$(dirname "$0")/../../.." && pwd)/artifacts/dashboard-redesign/${LABEL}"
BASE="${DASHBOARD_URL:-http://127.0.0.1:5175}"
mkdir -p "$OUT"

tabs=(
  "overview:/"
  "design-system:/design-system"
  "chat:/chat"
  "agents-list:/agents"
  "agents-detail:/agents/lyra"
  "jobs:/jobs"
  "fleet:/fleet"
  "ops:/ops"
)

for entry in "${tabs[@]}"; do
  name="${entry%%:*}"
  path="${entry#*:}"
  playwright screenshot --browser chromium --viewport-size=1440,900 \
    "${BASE}${path}" "${OUT}/${name}.png"
  echo "→ ${OUT}/${name}.png"
done