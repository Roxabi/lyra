#!/usr/bin/env bash
# deploy/fleet-digest-poll.sh — host-side GHCR digest poll for /fleet STALE_IMAGE badge
#
# Writes ~/.roxabi/factory/state/fleet-digests.json (hub reads via factory-data.volume).
# Invoked by factory-fleet-digest-poll.timer and at end of factory-post-autoupdate.

set -euo pipefail

source "$(dirname "$0")/lib/deploy-common.sh"

main() {
    mkdir -p "${HOME}/.roxabi/factory/state"
    uv run --project "${FACTORY_DIR}" python "${FACTORY_DIR}/tools/fleet_digest_poll.py" \
        --output "${HOME}/.roxabi/factory/state/fleet-digests.json"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi