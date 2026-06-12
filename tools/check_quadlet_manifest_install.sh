#!/usr/bin/env bash
# tools/check_quadlet_manifest_install.sh
#
# deploy/quadlet.toml is the authoritative unit manifest, but the install/restart
# paths are hand-enumerated: a component declared in the manifest is NOT deployed
# unless it also appears in
#   1. the Makefile `quadlet-install` recipe (cp, or render_quadlet.py --dest),
#   2. the UNITS array in deploy/quadlet-install-verify.sh,
#   3. the structural-drift restart fan-out in deploy/converge.sh
#      (factory-nats is exempt — it has its own _restart_nats path).
#
# Gap class (#1867): PR #1858 shipped factory-omp.container + its manifest entry,
# converge kept reporting "Converge complete" while the unit was never installed.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

fail=0
count=0
while IFS= read -r container; do
    count=$((count + 1))
    unit="${container%.container}"

    # Unit names are interpolated into the grep -E patterns below — reject
    # anything outside the safe charset rather than risk a mis-matching regex.
    if ! [[ "${unit}" =~ ^[a-z0-9-]+$ ]]; then
        echo "FAIL: ${container} — unit name must match ^[a-z0-9-]+\$"
        fail=1
        continue
    fi

    # Template-rendered units (telegram/discord) hit the first arm via the
    # .container.tmpl source-path substring; the QUADLET_DIR arm matches the
    # rendered --dest line. Either is acceptable evidence of install wiring.
    if ! grep -qF "deploy/quadlet/${container}" Makefile \
       && ! grep -qF "QUADLET_DIR)/${container}" Makefile; then
        echo "FAIL: ${container} is declared in deploy/quadlet.toml but never installed by 'make quadlet-install'"
        fail=1
    fi

    if ! grep -qE "^[[:space:]]+${unit}$" deploy/quadlet-install-verify.sh; then
        echo "FAIL: ${unit} is missing from the UNITS array in deploy/quadlet-install-verify.sh"
        fail=1
    fi

    if [ "${unit}" != "factory-nats" ] \
       && ! grep -E '^[[:space:]]*for svc in ' deploy/converge.sh \
            | grep -qE "[[:space:]]${unit}([[:space:]]|;)"; then
        echo "FAIL: ${unit} is missing from the structural restart fan-out in deploy/converge.sh"
        fail=1
    fi
done < <(grep -oE '^container = "[^"]+"' deploy/quadlet.toml | cut -d'"' -f2)

if [ "${count}" -eq 0 ]; then
    echo "FAIL: no containers parsed from deploy/quadlet.toml — gate validated nothing"
    exit 1
fi

if [ "${fail}" -ne 0 ]; then
    echo ""
    echo "Every [component.*] container in deploy/quadlet.toml must be wired into the"
    echo "Makefile quadlet-install recipe, the quadlet-install-verify.sh UNITS array,"
    echo "and the converge.sh restart fan-out — see #1867."
    exit 1
fi

echo "quadlet manifest install check passed"
