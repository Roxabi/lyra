#!/usr/bin/env bash
# deploy/converge.sh — atomic, idempotent, change-gated deploy (factory-hub hosts only)
#
# Quadlet install/prune: ~/projects/deploy.sh (role-aware SSOT).
# Factory-specific: template render, bot init, NATS auth, secrets, restarts.
#
# Usage: make converge
# Or directly: bash deploy/converge.sh

set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/deploy-common.sh"
# shellcheck source=lib/quadlet-units.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/quadlet-units.sh"

# M₁ only — see ~/projects/hosts.toml + lib/cluster_plan.py
require_host_role factory-hub

# Guard against concurrent runs (exit 0 if locked)
# NOTE: with_deploy_lock is called at the END of this file, after _do_converge is defined.

_do_converge() {
    # 1) Change-gate: classify drift (none / auth / structural)
    local _last _current _drift_kind
    _last=$(read_convergence_state)
    _current=$(compute_convergence_state)
    _drift_kind=$(_classify_drift "${_last}" "${_current}")

    if [ "${_drift_kind}" = "none" ]; then
        op_log converge_skip drift=none
        echo "Already converged — nothing to do."
        exit 0
    fi

    op_log converge_start drift="${_drift_kind}"
    echo "==> Convergence drift detected (${_drift_kind}) — beginning deploy..."

    # 2) Pull factory staging
    echo "==> factory: pulling staging..."
    require_clean_tree "${FACTORY_DIR}"
    (cd "${FACTORY_DIR}" && git pull --ff-only origin staging)

    # 3) Role-aware Quadlet install + orphan prune (SSOT: deploy.sh × hosts.toml)
    echo "==> cluster: installing role-matched Quadlets (deploy.sh --prune)..."
    bash "${PROJECTS_DIR}/deploy.sh" --prune

    # 4) Factory-specific render (telegram/discord templates, bot init, aux perms)
    echo "==> factory: rendering templates + aux units..."
    make -C "${FACTORY_DIR}" quadlet-install NO_RESTART=1

    # 5) Pull voiceCLI if present (HEAD tracked in convergence stamp)
    VOICE_DIR="${VOICE_DIR:-${HOME}/projects/voiceCLI}"
    if [ -d "${VOICE_DIR}/.git" ]; then
        echo "==> voiceCLI: pulling staging..."
        require_clean_tree "${VOICE_DIR}"
        (cd "${VOICE_DIR}" && git pull --ff-only origin staging)
    fi

    # 6) Regenerate auth.conf (bind mount — host file is source of truth, no secret to rotate)
    echo "==> NATS: regenerating auth.conf..."
    factory-acl genkeys --regen-authconf

    # 7) Install Podman secrets (factory-nats-auth is no longer a secret — bind mount per ADR-085)
    echo "==> NATS: installing Podman secrets..."
    bash "${FACTORY_DIR}/deploy/install.sh" --secrets-only

    # 8) Restart NATS (always — converge cannot prove a pure identity-add; #1390)
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

        # 9) Restart factory NATS clients (only on structural drift)
        echo "==> Factory: restarting containers..."
        local failed=""
        local _min_units
        local -a _all_svcs _client_svcs
        mapfile -t _all_svcs < <(quadlet_containers)
        # Lower bound derived from deploy/quadlet.toml at runtime; fail-fast on empty/partial parse — auto-updates when components are added.
        _min_units=$(grep -cE '^\[component\.' "$(dirname "${BASH_SOURCE[0]}")/quadlet.toml")
        [[ ${#_all_svcs[@]} -ge ${_min_units} ]] || { echo "ERROR: quadlet_containers returned ${#_all_svcs[@]} units (<${_min_units})" >&2; exit 1; }
        mapfile -t _client_svcs < <(printf '%s\n' "${_all_svcs[@]}" | grep -v '^factory-nats$' || true)
        for svc in "${_client_svcs[@]}"; do
            systemctl --user restart "${svc}" \
                || { echo "ERROR: restart ${svc} failed"; failed="${failed} ${svc}"; }
        done
        [ -z "${failed}" ] || { echo "ERROR: restart failed for:${failed}"; exit 1; }

        # 10) Restart voiceCLI if present (only on structural drift)
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

    # 11) Record convergence stamp — compute the post-converge state once and stamp
    # exactly that, instead of letting write_convergence_state recompute independently
    # (TOCTOU: a second compute could observe drift that occurred after the last step).
    local _final
    _final=$(compute_convergence_state)
    write_convergence_state "${_final}"

    op_log converge_complete drift="${_drift_kind}" exit=0
    echo "==> Converge complete."
}

with_deploy_lock _do_converge
