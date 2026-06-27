#!/usr/bin/env bash
# goal-1771-gates.sh — delegates to canonical `make qg` (no wrapper markers).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec make qg