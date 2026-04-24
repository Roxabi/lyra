#!/bin/bash
# deploy-lib.sh — shared Quadlet deploy library for the Roxabi ecosystem.
# Installed from lyra @ <commit-sha>
# DEPLOY_LIB_VERSION=1.0.0
#
# Interface: callers set the variables below, then call run_deploy "$@".
#
#   PROJECT          — short name, e.g. "lyra", "voicecli"
#   PROJECT_DIR      — checkout path, e.g. "$HOME/projects/lyra"
#   EXTRA_REPOS      — space-separated list of extra repos to check (optional)
#                      Each entry: "<name>:<path>:<upgrade-hook>" where <upgrade-hook>
#                      is a shell function name called after `git pull` succeeds.
#   IMAGE            — OCI image, e.g. "localhost/lyra:latest"
#   DOCKERFILE       — path relative to PROJECT_DIR, default "Dockerfile"
#   HUB_SERVICE      — primary service to start first and gate the rest on (optional)
#   ADAPTER_SERVICES — space-separated services restarted after HUB_SERVICE is active
#   ENV_FILES_DIR    — directory holding per-role env files, e.g. "$HOME/.lyra/env"
#   ENV_FILES        — space-separated list of <role> names expected under ENV_FILES_DIR
#                      (script verifies "<role>.env" exists with mode 0600)
#   LOG_FILE         — path to deploy log
#   FAIL_FILE        — path to SHA skip-list
#   PROJECT_TEST_CMD — test command to run after pull, default "uv run pytest --tb=short -q"

set -euo pipefail

# ── Required variable guard ───────────────────────────────────────────────────

_require_var() {
    local var="$1"
    if [[ -z "${!var:-}" ]]; then
        echo "ERROR: deploy-lib.sh: required variable \$$var is unset." >&2
        exit 1
    fi
}

# ── log ───────────────────────────────────────────────────────────────────────
# Timestamped output to stdout and LOG_FILE.

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE:?LOG_FILE is required}"
}

# ── check_repo <name> <dir> <upgrade-hook> ────────────────────────────────────
# Fetch origin/staging, compare SHAs, handle fail-file, pull, run tests,
# call upgrade-hook on success.
# Prints "true" to stdout if the repo was updated; nothing on no-op or skip.
#
# Sets the variable _REPO_UPDATED_<name> (uppercased) to "true" when updated.
# Callers should read this via _repo_was_updated <name>.

check_repo() {
    local name="$1"
    local dir="$2"
    local upgrade_hook="${3:-}"

    local test_cmd="${PROJECT_TEST_CMD:-uv run pytest --tb=short -q}"
    local var_name
    var_name="REPO_UPDATED_$(echo "$name" | tr '[:lower:]' '[:upper:]' | tr '-' '_')"

    eval "${var_name}=false"

    if [[ ! -d "$dir/.git" ]]; then
        log "WARN: $name: $dir is not a git repository — skipping"
        return 0
    fi

    cd "$dir"
    timeout 30 git fetch origin staging 2>&1 | tee -a "$LOG_FILE"

    local local_sha remote_sha
    local_sha=$(git rev-parse HEAD)
    remote_sha=$(git rev-parse origin/staging)

    if [[ "$local_sha" == "$remote_sha" ]]; then
        return 0
    fi

    # Skip SHAs marked as bad — prevents pull/test/fail/rollback loops.
    # Delete $FAIL_FILE to force a retry on a known-failing SHA.
    if [[ -f "$FAIL_FILE" ]] && grep -Fxq "$remote_sha" "$FAIL_FILE"; then
        # known-failing SHA — wait silently for a new commit
        return 0
    fi

    log "$name: new version $local_sha -> $remote_sha"

    # Reset generated files that may differ between machines (e.g. uv.lock after re-lock).
    git checkout -- uv.lock 2>/dev/null || true
    timeout 30 git pull origin staging 2>&1 | tee -a "$LOG_FILE"
    timeout 60 uv sync --all-extras --frozen 2>&1 | tee -a "$LOG_FILE"

    if [[ "$name" == "$PROJECT" ]]; then
        # Only run tests for the primary project repo.
        if ! timeout 120 $test_cmd 2>&1 | tee -a "$LOG_FILE"; then
            log "ERROR: $name tests failed for $remote_sha — rolling back and marking SHA as bad."
            echo "$remote_sha" >> "$FAIL_FILE"
            git reset --hard "$local_sha" 2>&1 | tee -a "$LOG_FILE"
            uv sync --all-extras --frozen 2>&1 | tee -a "$LOG_FILE"
            exit 1
        fi
        # Clear fail log on success so it does not grow unbounded.
        rm -f "$FAIL_FILE"
    fi

    eval "${var_name}=true"

    # Run the caller-supplied upgrade hook (e.g. uv sync --upgrade-package voicecli).
    if [[ -n "$upgrade_hook" ]] && declare -f "$upgrade_hook" > /dev/null 2>&1; then
        log "$name: running upgrade hook: $upgrade_hook"
        "$upgrade_hook"
    fi
}

# _repo_was_updated <name> — returns 0 (true) if check_repo updated that repo.
_repo_was_updated() {
    local var_name
    var_name="REPO_UPDATED_$(echo "$1" | tr '[:lower:]' '[:upper:]' | tr '-' '_')"
    [[ "${!var_name:-false}" == "true" ]]
}

# ── verify_env_files ──────────────────────────────────────────────────────────
# Enforce that each <role>.env under ENV_FILES_DIR exists with mode 0600.

verify_env_files() {
    if [[ -z "${ENV_FILES:-}" ]]; then
        return 0
    fi

    log "Verifying env files in ${ENV_FILES_DIR:?ENV_FILES_DIR is required}..."

    local role env_file mode
    for role in $ENV_FILES; do
        env_file="${ENV_FILES_DIR}/${role}.env"
        if [[ ! -f "$env_file" ]]; then
            log "ERROR: missing $env_file"
            log "  Copy deploy/quadlet/${role}.env.example → $env_file and fill in secrets"
            exit 1
        fi
        mode=$(stat -c %a "$env_file")
        if [[ "$mode" != "600" ]]; then
            log "ERROR: $env_file has mode $mode, expected 600"
            log "  Run: chmod 600 $env_file"
            exit 1
        fi
    done

    log "Env files OK."
}

# ── build_image ───────────────────────────────────────────────────────────────
# Tag rollback, build IMAGE from DOCKERFILE, capture digest, verify no mid-flight tamper.
# Writes image ID to $HOME/.<project>/.image-digest.

build_image() {
    _require_var PROJECT
    _require_var PROJECT_DIR
    _require_var IMAGE

    local dockerfile="${DOCKERFILE:-Dockerfile}"
    local digest_dir="$HOME/.${PROJECT}"

    cd "$PROJECT_DIR"

    log "Tagging current image as rollback..."
    podman tag "${IMAGE}" "${IMAGE%:*}:rollback" 2>/dev/null \
        || log "No existing image to tag (first deploy)"

    log "Building ${IMAGE}..."
    timeout 300 podman build -f "$dockerfile" -t "$IMAGE" . 2>&1 | tee -a "$LOG_FILE"
    log "Image build complete."

    mkdir -p "$digest_dir"
    EXPECTED_IMAGE_ID=$(podman inspect --format '{{.Id}}' "$IMAGE")
    log "Built image ID: $EXPECTED_IMAGE_ID"
    echo "$EXPECTED_IMAGE_ID" > "${digest_dir}/.image-digest"
}

# ── restart_services ──────────────────────────────────────────────────────────
# Verify image digest (tamper check), remove containers, restart HUB_SERVICE,
# readiness loop (12×5s + 2s stabilization), restart ADAPTER_SERVICES, final
# healthy loop for all services.
#
# EXPECTED_IMAGE_ID must be set (by build_image or the caller).

restart_services() {
    _require_var IMAGE

    local current_image_id
    current_image_id=$(podman inspect --format '{{.Id}}' "$IMAGE")

    if [[ "$current_image_id" != "$EXPECTED_IMAGE_ID" ]]; then
        log "ERROR: ${IMAGE} image ID changed between build and restart."
        log "  Expected: $EXPECTED_IMAGE_ID"
        log "  Current:  $current_image_id"
        log "  Possible supply-chain tampering. Aborting deploy."
        exit 1
    fi
    log "Image ID verified — proceeding with restart."

    verify_env_files

    local all_services="${HUB_SERVICE:-} ${ADAPTER_SERVICES:-}"
    all_services="${all_services# }"  # strip leading space if HUB_SERVICE is empty

    log "Removing old containers to force new image pickup..."
    # shellcheck disable=SC2086
    podman rm -f $all_services 2>/dev/null || true

    if [[ -n "${HUB_SERVICE:-}" ]]; then
        log "Restarting ${HUB_SERVICE}.service..."
        systemctl --user restart "${HUB_SERVICE}.service" 2>&1 | tee -a "$LOG_FILE"

        # Hub readiness loop: gate adapter restart until hub is active and stable.
        log "Waiting for ${HUB_SERVICE}.service to reach active (running)..."
        local hub_ready=false
        local i=1
        while [[ "$i" -le 12 ]]; do
            sleep 5
            local hub_state
            hub_state=$(systemctl --user is-active "${HUB_SERVICE}.service" 2>/dev/null || true)
            if [[ "$hub_state" == "active" ]]; then
                # Stabilization hold: re-check after 2s to filter activating→active→failed flaps.
                sleep 2
                hub_state=$(systemctl --user is-active "${HUB_SERVICE}.service" 2>/dev/null || true)
                if [[ "$hub_state" == "active" ]]; then
                    log "${HUB_SERVICE}.service is active — restarting adapters"
                    hub_ready=true
                    break
                fi
                log "${HUB_SERVICE}.service flapped (attempt $i/12)"
                i=$(( i + 1 ))
                continue
            fi
            log "${HUB_SERVICE}.service state: ${hub_state:-unknown} (attempt $i/12)"
            i=$(( i + 1 ))
        done

        if [[ "$hub_ready" == false ]]; then
            log "ERROR: ${HUB_SERVICE}.service did not reach active after 60s — stopping adapters and aborting"
            if [[ -n "${ADAPTER_SERVICES:-}" ]]; then
                # shellcheck disable=SC2086
                systemctl --user stop $ADAPTER_SERVICES 2>&1 | tee -a "$LOG_FILE"
            fi
            exit 1
        fi
    fi

    if [[ -n "${ADAPTER_SERVICES:-}" ]]; then
        log "Restarting ${ADAPTER_SERVICES}..."
        # shellcheck disable=SC2086
        systemctl --user restart $ADAPTER_SERVICES 2>&1 | tee -a "$LOG_FILE"
    fi

    # Verify all services reached active.
    log "Verifying services..."
    local all_units=""
    [[ -n "${HUB_SERVICE:-}"      ]] && all_units="${HUB_SERVICE}.service"
    [[ -n "${ADAPTER_SERVICES:-}" ]] && {
        local svc
        for svc in $ADAPTER_SERVICES; do
            all_units="${all_units} ${svc}.service"
        done
    }
    all_units="${all_units# }"

    local healthy=false
    local i=1
    while [[ "$i" -le 12 ]]; do
        sleep 5
        local failed=0
        local unit
        for unit in $all_units; do
            local state
            state=$(systemctl --user is-active "$unit" 2>/dev/null || true)
            if [[ "$state" != "active" ]]; then
                failed=$(( failed + 1 ))
            fi
        done
        if [[ "$failed" -eq 0 ]]; then
            healthy=true
            break
        fi
        log "Waiting for services... ($failed not active, attempt $i/12)"
        i=$(( i + 1 ))
    done

    if [[ "$healthy" == false ]]; then
        log "ERROR: Some services failed to reach active after 60s:"
        # shellcheck disable=SC2086
        systemctl --user status $all_units --no-pager --lines=0 2>&1 | tee -a "$LOG_FILE"
        exit 1
    fi
}

# ── run_deploy ────────────────────────────────────────────────────────────────
# Top-level orchestrator. Call after setting all required variables.
#
# Flow:
#   1. check_repo for PROJECT_DIR
#   2. check_repo for each entry in EXTRA_REPOS
#   3. Early-exit if nothing updated
#   4. build_image
#   5. restart_services (includes verify_env_files)
#   6. Log tags summary

run_deploy() {
    _require_var PROJECT
    _require_var PROJECT_DIR
    _require_var IMAGE
    _require_var LOG_FILE
    _require_var FAIL_FILE

    mkdir -p "$(dirname "$LOG_FILE")"

    # Check primary project repo (always; runs tests).
    check_repo "$PROJECT" "$PROJECT_DIR" ""

    # Check extra repos (no tests; caller supplies upgrade hook).
    local entry name path hook
    for entry in ${EXTRA_REPOS:-}; do
        IFS=':' read -r name path hook <<< "$entry"
        check_repo "$name" "$path" "${hook:-}"
    done

    # Early-exit if nothing changed.
    local any_updated=false
    if _repo_was_updated "$PROJECT"; then
        any_updated=true
    fi
    for entry in ${EXTRA_REPOS:-}; do
        IFS=':' read -r name _ _ <<< "$entry"
        if _repo_was_updated "$name"; then
            any_updated=true
        fi
    done

    if [[ "$any_updated" == false ]]; then
        exit 0
    fi

    build_image
    restart_services

    # Tags summary.
    local tags=""
    if _repo_was_updated "$PROJECT"; then
        tags="${tags} ${PROJECT}=$(cd "$PROJECT_DIR" && git rev-parse --short HEAD)"
    fi
    for entry in ${EXTRA_REPOS:-}; do
        IFS=':' read -r name path _ <<< "$entry"
        if _repo_was_updated "$name"; then
            tags="${tags} ${name}=$(cd "$path" && git rev-parse --short HEAD)"
        fi
    done
    log "Deploy complete:${tags}"
}
