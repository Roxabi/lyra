#!/usr/bin/env bash
# tools/check_quadlet_component_source.sh
#
# S2-class regression guard (#2038): every component declared in
# deploy/quadlet.toml must have a corresponding source file on disk.
#
# For each  container = "X.container"  entry in deploy/quadlet.toml, assert
# that at least one of these paths exists:
#
#   deploy/quadlet/X.container        (static unit)
#   deploy/quadlet/X.container.tmpl   (template-rendered unit, e.g. telegram/discord)
#
# A manifest entry with no matching source means the unit can never be
# installed or converged — the S2 gap that shipped factory-omp.container
# without a Containerfile being present in the right form.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

fail=0
count=0

while IFS= read -r container; do
    count=$((count + 1))

    # Reject names outside the safe charset to prevent subtle path tricks.
    unit="${container%.container}"
    if ! [[ "${unit}" =~ ^[a-z0-9-]+$ ]]; then
        echo "FAIL: ${container} — unit name must match ^[a-z0-9-]+\$"
        fail=1
        continue
    fi

    static="deploy/quadlet/${container}"
    tmpl="deploy/quadlet/${container}.tmpl"

    if [[ ! -f "${static}" ]] && [[ ! -f "${tmpl}" ]]; then
        echo "FAIL: ${container} is declared in deploy/quadlet.toml but has no source file"
        echo "      checked: ${static}"
        echo "      checked: ${tmpl}"
        fail=1
    fi

done < <(grep -oE '^container[[:space:]]*=[[:space:]]*"[^"]+"' deploy/quadlet.toml | cut -d'"' -f2)

if [ "${count}" -eq 0 ]; then
    echo "FAIL: no containers parsed from deploy/quadlet.toml — gate validated nothing"
    exit 1
fi

if [ "${fail}" -ne 0 ]; then
    echo ""
    echo "Every [component.*] container in deploy/quadlet.toml must have a source file"
    echo "at deploy/quadlet/<name>.container or deploy/quadlet/<name>.container.tmpl."
    echo "Add the missing file or remove the stale manifest entry — see #2038."
    exit 1
fi

echo "quadlet component source check passed (${count} containers)"
