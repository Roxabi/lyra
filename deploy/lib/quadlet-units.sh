# deploy/lib/quadlet-units.sh — sourced, never executed
#
# Provides quadlet_containers() — parses deploy/quadlet.toml and emits
# enabled container names (one per line, stripped .container suffix) in
# declaration order. Components with disabled = true are skipped.
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
    python3 - "${toml}" <<'PY' || return 1
import sys, tomllib, pathlib

toml_path = pathlib.Path(sys.argv[1])
with toml_path.open("rb") as f:
    data = tomllib.load(f)

out: list[str] = []
for sub in data.get("component", {}).values():
    if sub.get("disabled"):
        continue
    container = sub.get("container")
    if not container:
        continue
    name = container.removesuffix(".container")
    out.append(name)

if not out:
    print("ERROR: quadlet_containers parsed 0 enabled containers", file=sys.stderr)
    raise SystemExit(1)

for name in out:
    print(name)
PY
}

quadlet_enabled_component_count() {
    local toml
    toml="$(dirname "${BASH_SOURCE[0]}")/../quadlet.toml"
    [[ -f "${toml}" ]] || { echo "ERROR: quadlet.toml not found at ${toml}" >&2; return 1; }
    python3 - "${toml}" <<'PY' || return 1
import sys, tomllib, pathlib

toml_path = pathlib.Path(sys.argv[1])
with toml_path.open("rb") as f:
    data = tomllib.load(f)

count = sum(
    1
    for sub in data.get("component", {}).values()
    if not sub.get("disabled") and sub.get("container")
)
if count == 0:
    raise SystemExit(1)
print(count)
PY
}