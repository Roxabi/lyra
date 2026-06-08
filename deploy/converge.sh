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
    # 1) Change-gate: classify drift (none / auth / structural)
    local _last _current _drift_kind
    _last=$(read_convergence_state)
    _current=$(compute_convergence_state)
    _drift_kind=$(_classify_drift "${_last}" "${_current}")

    if [ "${_drift_kind}" = "none" ]; then
        echo "Already converged — nothing to do."
        exit 0
    fi

    echo "==> Convergence drift detected (${_drift_kind}) — beginning deploy..."

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

    # 5) Regenerate auth.conf (bind mount — host file is source of truth, no secret to rotate)
    echo "==> NATS: regenerating auth.conf..."
    factory-acl genkeys --regen-authconf

    # 6) Install Podman secrets (factory-nats-auth is no longer a secret — bind mount per ADR-085)
    echo "==> NATS: installing Podman secrets..."
    bash "${FACTORY_DIR}/deploy/install.sh" --secrets-only

    # 7) Restart NATS (always — converge cannot prove a pure identity-add; #1390)
    # NB: plain restart, NOT `restart --wait` — `--wait` blocks until the unit
    # *deactivates*, which never happens for a long-running daemon, so it hung the
    # entire converge (#1738). The is-active poll below is the readiness gate.
    _restart_nats() {
        echo "==> NATS: restarting factory-nats..."
        systemctl --user restart factory-nats
        for _ in $(seq 1 30); do
            systemctl --user is-active --quiet factory-nats && break
            if systemctl --user is-failed --quiet factory-nats; then
                echo "ERROR: factory-nats entered failed state"
                exit 1
            fi
            sleep 1
        done
        systemctl --user is-active --quiet factory-nats \
            || { echo "ERROR: factory-nats failed to reach active state within 30 s"; exit 1; }
    }

    if [ "${_drift_kind}" = "auth" ]; then
        # auth-only drift: restart NATS server so fresh ACL eval is forced on all subjects
        # (#1390 — SIGHUP is unsafe for revocations; converge cannot distinguish pure add).
        # Clients auto-reconnect via allow_reconnect — no explicit client fan-out needed.
        echo "==> NATS: auth-only drift → restart factory-nats only (clients reconnect via allow_reconnect)."
        _restart_nats
    else
        # structural drift: full restart required (image/unit/voiceCLI changed, or first run)
        echo "==> NATS: structural drift → restarting factory-nats + clients..."
        _restart_nats

        # 8) Restart factory NATS clients (only on structural drift)
        echo "==> Lyra: restarting containers..."
        local failed=""
        for svc in factory-hub factory-telegram factory-discord factory-clipool factory-turn-writer factory-gh-helper factory-blobstore; do
            systemctl --user restart "${svc}" \
                || { echo "ERROR: restart ${svc} failed"; failed="${failed} ${svc}"; }
        done
        [ -z "${failed}" ] || { echo "ERROR: restart failed for:${failed}"; exit 1; }

        # 9) Restart voiceCLI if present (only on structural drift)
        if [ -d "${VOICE_DIR}/.git" ]; then
            echo "==> voiceCLI: restarting containers..."
            failed=""
            for svc in voicecli-tts voicecli-stt; do
                systemctl --user restart "${svc}" \
                    || { echo "ERROR: restart ${svc} failed"; failed="${failed} ${svc}"; }
            done
            [ -z "${failed}" ] || { echo "ERROR: restart failed for:${failed}"; exit 1; }
        fi
    fi

    # 10) Record convergence stamp
    write_convergence_state

    echo "==> Converge complete."
}

with_deploy_lock _do_converge
