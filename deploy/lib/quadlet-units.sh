# deploy/lib/quadlet-units.sh — sourced, never executed
#
# Provides quadlet_containers() — parses deploy/quadlet.toml and emits
# the container names (one per line, stripped .container suffix) in
# declaration order.
#
# Usage (from a script in deploy/ or deploy/lib/):
#   source "$(dirname "${BASH_SOURCE[0]}")/../lib/quadlet-units.sh"
#   mapfile -t UNITS < <(quadlet_containers)
#
# Safe to source multiple times — function is idempotent.
quadlet_containers() {
    local toml
    toml="$(dirname "${BASH_SOURCE[0]}")/../quadlet.toml"   # cwd-independent (¬$0)
    [[ -f "${toml}" ]] || { echo "ERROR: quadlet.toml not found at ${toml}" >&2; return 1; }
    local -a out
    mapfile -t out < <(grep -oE '^container[[:space:]]*=[[:space:]]*"[^"]+"' "${toml}" \
        | cut -d'"' -f2 | sed 's/\.container$//' | tr -d ' \t')
    [[ ${#out[@]} -gt 0 ]] || { echo "ERROR: quadlet_containers parsed 0 containers from ${toml}" >&2; return 1; }
    printf '%s\n' "${out[@]}"
}
