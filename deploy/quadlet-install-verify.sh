#!/usr/bin/env bash
# deploy/quadlet-install-verify.sh
#
# Step 2-4 of `make quadlet-install`:
#   1. daemon-reload  — triggers Quadlet generator
#   2. try-restart    — restarts each unit if currently running; starts it if not
#   3. verify         — checks `is-active`; dumps last 20 journal lines on failure
#
# Invoked by the Makefile.  Skip via:  make quadlet-install NO_RESTART=1
#
# Exits non-zero if any unit fails to reach `active`.
set -euo pipefail
# shellcheck source=lib/env.sh
source "$(dirname "$0")/lib/env.sh"

# Units derived from .container files.  Quadlet maps <name>.container → <name>.service.
UNITS=(
    lyra-nats
    lyra-hub
    lyra-telegram
    lyra-discord
    lyra-clipool
    lyra-blobstore
    lyra-turn-writer
)

# ── 1. Reload daemon so Quadlet generates fresh .service files ────────────────
echo "[quadlet] daemon-reload ..."
systemctl --user daemon-reload

# ── 2. Restart-or-start each unit ────────────────────────────────────────────
for unit in "${UNITS[@]}"; do
    service="${unit}.service"
    if systemctl --user is-active --quiet "$service" 2>/dev/null; then
        echo "[quadlet] restarting $service ..."
        systemctl --user restart "$service"
    else
        echo "[quadlet] starting $service ..."
        systemctl --user start "$service" || true
        # `start` on a unit that is already activating is fine; `|| true`
        # prevents set -e from aborting here — the is-active check below
        # is the real gate.
    fi
done

# ── 3. Verify each unit reached `active` ─────────────────────────────────────
FAILED=()
for unit in "${UNITS[@]}"; do
    service="${unit}.service"
    # Give the unit up to 10 s to reach active (container pull / init can be slow
    # on first run, but on subsequent runs the image is local so this is fast).
    for i in $(seq 1 10); do
        if systemctl --user is-active --quiet "$service" 2>/dev/null; then
            echo "[quadlet] $service — active"
            break
        fi
        sleep 1
        if [ "$i" -eq 10 ]; then
            FAILED+=("$unit")
        fi
    done
done

if [ ${#FAILED[@]} -eq 0 ]; then
    echo "[quadlet] all units active."
    exit 0
fi

# ── Failure path: dump journal and exit non-zero ──────────────────────────────
echo ""
echo "[quadlet] ERROR: the following units did not reach active state:"
for unit in "${FAILED[@]}"; do
    echo "  - ${unit}.service"
done
echo ""
for unit in "${FAILED[@]}"; do
    echo "──────────────────────────────────────────────────────────────────────"
    echo "  journalctl --user -u ${unit}.service  (last 20 lines)"
    echo "──────────────────────────────────────────────────────────────────────"
    journalctl --user -u "${unit}.service" -n 20 --no-pager 2>/dev/null || \
        echo "  (no journal entries found)"
    echo ""
done
echo "[quadlet] Deploy failed.  Fix the unit(s) above and re-run \`make quadlet-install\`."
echo "          To copy files without restarting: \`make quadlet-install NO_RESTART=1\`"
exit 1
