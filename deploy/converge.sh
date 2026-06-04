#!/usr/bin/env bash
# deploy/converge.sh — atomic, idempotent, change-gated deploy
#
# Usage: make converge
# Or directly: bash deploy/converge.sh

set -euo pipefail

source "$(dirname "$0")/lib/deploy-common.sh"

# Guard against concurrent runs (exit 0 if locked)
# NOTE: with_deploy_lock is called at the END of this file, after _do_converge is defined.

_do_converge() {
    # 1) Change-gate: already converged?
    if is_converged; then
        echo "Already converged — nothing to do."
        exit 0
    fi

    echo "==> Convergence drift detected — beginning deploy..."

    # 2) Pull factory staging
    echo "==> factory: pulling staging..."
    (cd "${FACTORY_DIR}" && git pull --ff-only origin staging)

    # 3) Install factory quadlet units (no restart)
    echo "==> factory: installing quadlet units..."
    make -C "${FACTORY_DIR}" quadlet-install NO_RESTART=1

    # 4) Pull voiceCLI if present
    VOICE_DIR="${VOICE_DIR:-${HOME}/projects/voiceCLI}"
    if [ -d "${VOICE_DIR}/.git" ]; then
        echo "==> voiceCLI: pulling staging..."
        (cd "${VOICE_DIR}" && git pull --ff-only origin staging)
        echo "==> voiceCLI: installing quadlet units..."
        make -C "${VOICE_DIR}" quadlet-install NO_RESTART=1
    fi

    # 5) Regenerate auth.conf
    echo "==> NATS: regenerating auth.conf..."
    factory-acl genkeys --regen-authconf

    # 5a) Rotate factory-nats-auth Podman secret so NATS picks up the new auth.conf
    #     on the upcoming restart (type=mount secrets are stale until container restart;
    #     --replace here ensures the new tmpfs content is ready before step 7).
    echo "==> NATS: rotating factory-nats-auth secret..."
    podman secret create --replace factory-nats-auth "${FACTORY_NKEYS_DIR}/auth.conf"

    # 6) Install remaining secrets (skips factory-nats-auth — already replaced above)
    echo "==> NATS: installing Podman secrets..."
    bash "${FACTORY_DIR}/deploy/install.sh" --secrets-only

    # 7) Restart NATS (mount-typed secret refresh requires restart)
    echo "==> NATS: restarting factory-nats..."
    systemctl --user restart --wait factory-nats
    systemctl --user is-active --quiet factory-nats \
        || { echo "ERROR: factory-nats failed to reach active state"; exit 1; }

    # 8) Restart factory NATS clients
    echo "==> Lyra: restarting containers..."
    local failed=""
    for svc in factory-hub factory-telegram factory-discord factory-clipool factory-turn-writer factory-gh-helper factory-blobstore; do
        systemctl --user restart "${svc}" \
            || { echo "ERROR: restart ${svc} failed"; failed="${failed} ${svc}"; }
    done
    [ -z "${failed}" ] || { echo "ERROR: restart failed for:${failed}"; exit 1; }

    # 9) Restart voiceCLI if present
    if [ -d "${VOICE_DIR}/.git" ]; then
        echo "==> voiceCLI: restarting containers..."
        failed=""
        for svc in voicecli-tts voicecli-stt; do
            systemctl --user restart "${svc}" \
                || { echo "ERROR: restart ${svc} failed"; failed="${failed} ${svc}"; }
        done
        [ -z "${failed}" ] || { echo "ERROR: restart failed for:${failed}"; exit 1; }
    fi

    # 10) Record convergence stamp
    write_convergence_state

    echo "==> Converge complete."
}

with_deploy_lock _do_converge
