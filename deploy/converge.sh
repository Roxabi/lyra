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
    # deploy.sh tiered exit (#2037): 2 = hard (a unit's source file is missing → it cannot be
    # installed → abort). 1 = soft (a required secret is absent → the unit IS installed but won't
    # start until provisioned; secrets are installed at step 7 below and a genuinely-missing one
    # surfaces as a failed restart at step 9, so warn + continue rather than aborting the deploy on
    # one repo's not-yet-present secret). 0 = clean. Capture without tripping set -e.
    local _deploy_rc=0
    bash "${PROJECTS_DIR}/deploy.sh" --prune || _deploy_rc=$?
    if [ "${_deploy_rc}" -ge 2 ]; then
        # rc 2 = a unit source file is missing (deploy.sh logs the specifics above); rc >2 = a
        # deploy.sh CLI/runtime error. Either way the cluster install is unsafe — abort, and
        # propagate the code rather than asserting a single cause.
        echo "ERROR: deploy.sh failed (rc=${_deploy_rc}) — see its output above. Aborting converge." >&2
        exit "${_deploy_rc}"
    elif [ "${_deploy_rc}" -eq 1 ]; then
        echo "WARN: deploy.sh reported missing secret(s) (rc=1) — continuing; secrets install (step 7) + restart (step 9) enforce them." >&2
    fi

    # 4) Factory-specific render (telegram/discord templates, bot init, aux perms)
    echo "==> factory: rendering templates + aux units..."
    make -C "${FACTORY_DIR}" quadlet-install NO_RESTART=1

    # 4b) NO_RESTART=1 above also skips daemon-reload (the reload lives solely in
    # deploy/quadlet-install-verify.sh, which the NO_RESTART branch never runs). Reload
    # here, unconditionally, so systemd regenerates .service units from the Quadlet
    # content steps 3-4 just installed/rendered (bot add/remove → Secret= churn in
    # factory-telegram/-discord.container, image digest bumps, hardening edits) BEFORE
    # any restart below — else the restarts relaunch from the stale generated unit.
    echo "==> systemd: reloading user daemon (pick up Quadlet unit changes)..."
    systemctl --user daemon-reload

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
        # NB: grep -c exits 1 on zero matches (and 2 on a missing file); under set -e that would abort
        # the script BEFORE the friendly guard below, so catch it explicitly (this is the empty/corrupt
        # manifest case the guard exists to report).
        _min_units=$(quadlet_enabled_component_count) \
            || { echo "ERROR: quadlet.toml missing or has no enabled [component.*] sections (parse failure)" >&2; exit 1; }
        [[ ${#_all_svcs[@]} -ge ${_min_units} ]] || { echo "ERROR: quadlet_containers returned ${#_all_svcs[@]} units (<${_min_units})" >&2; exit 1; }
        mapfile -t _client_svcs < <(printf '%s\n' "${_all_svcs[@]}" | grep -v '^factory-nats$' || true)
        for svc in "${_client_svcs[@]}"; do
            systemctl --user restart "${svc}" \
                || { echo "ERROR: restart ${svc} failed"; failed="${failed} ${svc}"; }
        done
        [ -z "${failed}" ] || { echo "ERROR: restart failed for:${failed}"; exit 1; }

        # 10) Restart voiceCLI ONLY when its own HEAD actually changed — not on every
        # structural drift. voiceCLI is a separate product; a factory-only change (git HEAD,
        # image digest, or unit file) must not bounce it. field 4 of the fingerprint is
        # voicecli-head: git-head:units:auth:VOICECLI-HEAD:img-svc:img-stg. On first run
        # (_last="none") field 4 is empty ≠ current hash → restart, which is correct.
        local _last_voice _cur_voice
        _last_voice=$(printf '%s' "${_last}" | cut -d: -f4)
        _cur_voice=$(printf '%s' "${_current}" | cut -d: -f4)
        if [ -d "${VOICE_DIR}/.git" ] && [ "${_last_voice}" != "${_cur_voice}" ]; then
            echo "==> voiceCLI: HEAD changed (fingerprint field 4) → restarting containers..."
            failed=""
            for svc in voicecli-tts voicecli-stt; do
                systemctl --user restart "${svc}" \
                    || { echo "ERROR: restart ${svc} failed"; failed="${failed} ${svc}"; }
            done
            [ -z "${failed}" ] || { echo "ERROR: restart failed for:${failed}"; exit 1; }
        elif [ -d "${VOICE_DIR}/.git" ]; then
            echo "==> voiceCLI: HEAD unchanged → skip restart (factory-only structural drift)."
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

    if command -v factory >/dev/null 2>&1; then
        factory ops publish-host-event converge.completed \
            --payload-json "{\"drift_kind\":\"${_drift_kind}\"}" \
            || echo "WARN: converge.completed event publish failed (non-fatal)" >&2
    fi
}

with_deploy_lock _do_converge
