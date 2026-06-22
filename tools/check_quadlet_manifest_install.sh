#!/usr/bin/env bash
# tools/check_quadlet_manifest_install.sh
#
# deploy/quadlet.toml is the authoritative unit manifest.  A component declared
# in the manifest is NOT deployed unless it also appears in:
#   1. the Makefile `quadlet-install` recipe (cp, or render_quadlet.py --dest).
#
# Gap class (#1867): PR #1858 shipped factory-omp.container + its manifest entry,
# but the unit was never installed by the Makefile recipe.
#
# BIDIRECTIONAL CHECK (#1901):
#   Reverse direction: every unit FILE in deploy/quadlet/ (*.container,
#   *.volume, *.network, *.pod — excluding *.example, *.env) MUST be declared
#   in deploy/quadlet.toml.  For *.container.tmpl, strip .tmpl and check the
#   resulting name.  Any on-disk file with no matching declaration → FAIL.
#
# NETWORK / POD FORWARD CHECK (#1901):
#   [network.*] and [pod.*] entries in quadlet.toml are also install-evidenced
#   by the Makefile quadlet-install glob (*.network / *.pod) — same 2-arm check
#   as volumes.  These are not services, so no verify.sh / converge.sh arms.
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
    # rendered --dest line. A glob loop in the Makefile covering the file's
    # extension + the file existing on disk is also acceptable evidence.
    ext="${container##*.}"
    if ! grep -qF "deploy/quadlet/${container}" Makefile \
       && ! grep -qF "QUADLET_DIR)/${container}" Makefile \
       && ! { grep -qF "deploy/quadlet/*.${ext}" Makefile && [[ -f "deploy/quadlet/${container}" ]]; }; then
        echo "FAIL: ${container} is declared in deploy/quadlet.toml but never installed by 'make quadlet-install'"
        fail=1
    fi

done < <(grep -oE '^container[[:space:]]*=[[:space:]]*"[^"]+"' deploy/quadlet.toml | cut -d'"' -f2)

# Volume units: a [volume.*] declared in the manifest must also be cp'd by the
# Makefile quadlet-install recipe. If it is not, the Quadlet generator drops
# every .container that references it via Volume=/Requires= — the unit silently
# fails to generate and converge's restart fan-out errors every cycle (#1813:
# factory-omp-sessions.volume shipped + referenced but never installed → no
# factory-omp.service → 5-min NATS+clients bounce loop).
while IFS= read -r volume; do
    count=$((count + 1))
    ext="${volume##*.}"
    if ! grep -qF "deploy/quadlet/${volume}" Makefile \
       && ! { grep -qF "deploy/quadlet/*.${ext}" Makefile && [[ -f "deploy/quadlet/${volume}" ]]; }; then
        echo "FAIL: ${volume} is declared in deploy/quadlet.toml but never installed by 'make quadlet-install'"
        fail=1
    fi
done < <(grep -oE '^volume[[:space:]]*=[[:space:]]*"[^"]+"' deploy/quadlet.toml | cut -d'"' -f2)

# Network units: a [network.*] declared in the manifest must be install-evidenced
# by the Makefile quadlet-install recipe (same 2-arm check as volumes).
while IFS= read -r network; do
    count=$((count + 1))
    ext="${network##*.}"
    if ! grep -qF "deploy/quadlet/${network}" Makefile \
       && ! { grep -qF "deploy/quadlet/*.${ext}" Makefile && [[ -f "deploy/quadlet/${network}" ]]; }; then
        echo "FAIL: ${network} is declared in deploy/quadlet.toml but never installed by 'make quadlet-install'"
        fail=1
    fi
done < <(grep -oE '^network[[:space:]]*=[[:space:]]*"[^"]+"' deploy/quadlet.toml | cut -d'"' -f2)

# Pod units: a [pod.*] declared in the manifest must be install-evidenced by
# the Makefile quadlet-install recipe.
while IFS= read -r pod; do
    count=$((count + 1))
    ext="${pod##*.}"
    if ! grep -qF "deploy/quadlet/${pod}" Makefile \
       && ! { grep -qF "deploy/quadlet/*.${ext}" Makefile && [[ -f "deploy/quadlet/${pod}" ]]; }; then
        echo "FAIL: ${pod} is declared in deploy/quadlet.toml but never installed by 'make quadlet-install'"
        fail=1
    fi
done < <(grep -oE '^pod[[:space:]]*=[[:space:]]*"[^"]+"' deploy/quadlet.toml | cut -d'"' -f2)

# ── REVERSE DIRECTION: every on-disk unit file must be declared in quadlet.toml ─
# A single python3 heredoc reads quadlet.toml via tomllib, iterates deploy/quadlet/,
# and prints one FAIL line per undeclared file.  Using the heredoc-stdin pattern
# (python3 - FILE <<'PYEOF') keeps this safe under set -euo pipefail — unlike
# `read -r -d '' VAR << 'PYEOF'` which exits 1 at EOF and aborts the script.
#
# Excluded: *.example, *.env files.
# *.container.tmpl → strip .tmpl → checked as *.container.
_reverse_fails=$(python3 - deploy/quadlet.toml <<'PYEOF'
import sys, tomllib, pathlib

toml_path = pathlib.Path(sys.argv[1])
with toml_path.open("rb") as f:
    data = tomllib.load(f)

containers: set[str] = set()
volumes: set[str] = set()
networks: set[str] = set()
pods: set[str] = set()

# TOML nests sub-tables: [component.nats] → data["component"]["nats"].
# Iterate the sub-table VALUES (mirrors load_quadlet_required_secrets).
for sub in data.get("component", {}).values():
    if "container" in sub:
        containers.add(sub["container"])
for sub in data.get("volume", {}).values():
    if "volume" in sub:
        volumes.add(sub["volume"])
for sub in data.get("network", {}).values():
    if "network" in sub:
        networks.add(sub["network"])
for sub in data.get("pod", {}).values():
    if "pod" in sub:
        pods.add(sub["pod"])

quadlet_dir = pathlib.Path("deploy/quadlet")
for fpath in sorted(quadlet_dir.iterdir()):
    if not fpath.is_file():
        continue
    fname = fpath.name
    # Skip non-unit files
    if fname.endswith(".example") or fname.endswith(".env"):
        continue
    # Determine effective name and which set to check
    if fname.endswith(".container.tmpl"):
        effective = fname[:-len(".tmpl")]  # strip .tmpl → *.container
        declared = containers
    elif fname.endswith(".container"):
        effective = fname
        declared = containers
    elif fname.endswith(".volume"):
        effective = fname
        declared = volumes
    elif fname.endswith(".network"):
        effective = fname
        declared = networks
    elif fname.endswith(".pod"):
        effective = fname
        declared = pods
    else:
        # Unknown extension — not a Quadlet unit file, skip
        continue
    if effective not in declared:
        print(
            f"::error file=deploy/quadlet/{fname}::"
            f"FAIL (reverse): deploy/quadlet/{fname} has no matching declaration"
            f" in deploy/quadlet.toml"
        )
PYEOF
)
if [[ -n "${_reverse_fails}" ]]; then
    echo "" >&2
    echo "FAIL (reverse): on-disk unit files in deploy/quadlet/ not declared in deploy/quadlet.toml:" >&2
    while IFS= read -r line; do
        echo "  ${line}" >&2
        echo "${line}"
    done <<< "${_reverse_fails}"
    fail=1
fi

if [ "${count}" -eq 0 ]; then
    echo "FAIL: no containers parsed from deploy/quadlet.toml — gate validated nothing"
    exit 1
fi

if [ "${fail}" -ne 0 ]; then
    echo ""
    echo "Every [component.*] container in deploy/quadlet.toml must be wired into the"
    echo "Makefile quadlet-install recipe — see #1867."
    echo "Every [volume.*] volume must be cp'd by the quadlet-install recipe — see #1813."
    echo "Every unit FILE in deploy/quadlet/ must be declared in deploy/quadlet.toml — see #1901."
    exit 1
fi

echo "quadlet manifest install check passed"
