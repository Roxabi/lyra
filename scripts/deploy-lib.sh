#!/bin/bash
# DEPRECATED: replaced by podman-auto-update (see #929). Retained as offline fallback.
# deploy-lib.sh — shared Quadlet deploy library for the Roxabi ecosystem.
# Installed from lyra @ <commit-sha>
#
# Interface: callers set the variables below, then call run_deploy "$@".
#
#   PROJECT          — short name, e.g. "lyra", "voicecli"
#   PROJECT_DIR      — checkout path, e.g. "$HOME/projects/lyra"
#   PROJECT_BRANCH   — branch to track, default "staging"
#   EXTRA_REPOS      — newline-separated entries; each line is "name:path:hook"
#                      where hook is an optional shell function name. Paths must
#                      not contain colons. Empty lines are skipped.
#   IMAGE            — OCI image, e.g. "localhost/lyra:latest"
#   DOCKERFILE       — path relative to PROJECT_DIR, default "Dockerfile"
#   HUB_SERVICE      — primary service to start first and gate the rest on (optional)
#   ADAPTER_SERVICES — space-separated services restarted after HUB_SERVICE is active
#   ENV_FILES_DIR    — directory holding per-role env files, e.g. "$HOME/.lyra/env"
#   ENV_FILES        — space-separated list of <role> names expected under ENV_FILES_DIR
#                      (script verifies "<role>.env" exists with mode 0600)
#   LOG_FILE         — path to deploy log
#   FAIL_FILE        — path to SHA skip-list
#   PROJECT_TEST_CMD — test command to run after pull; empty string = skip tests.
#                      Example: "uv run pytest --tb=short -q"

set -euo pipefail

readonly DEPLOY_LIB_VERSION="1.0.0"
deploy_lib_version() { echo "$DEPLOY_LIB_VERSION"; }

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
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    printf '%s\n' "$msg"
    printf '%s\n' "$msg" >> "${LOG_FILE:?LOG_FILE is required}"
}

# ── Associative array for repo-update tracking ────────────────────────────────
declare -gA _REPO_UPDATED

# ── check_repo <name> <dir> <upgrade-hook> ────────────────────────────────────
# Fetch origin/$PROJECT_BRANCH, compare SHAs, handle fail-file, pull, run tests,
# call upgrade-hook on success.
#
# Sets _REPO_UPDATED[$name]=true when updated.
# Callers should read this via _repo_was_updated <name>.

check_repo() {
    local name="$1"
    local dir="$2"
    local upgrade_hook="${3:-}"
    local branch="${PROJECT_BRANCH:-staging}"

    # Validate name to prevent associative-array key injection.
    if [[ ! "$name" =~ ^[A-Za-z0-9_-]+$ ]]; then
        echo "ERROR: check_repo: invalid repo name '$name' (must match ^[A-Za-z0-9_-]+\$)" >&2
        exit 1
    fi

    _REPO_UPDATED["$name"]=false

    if [[ ! -d "$dir/.git" ]]; then
        log "WARN: $name: $dir is not a git repository — skipping"
        return 0
    fi

    # Use pushd/popd so the caller's working directory is not mutated.
    pushd "$dir" > /dev/null
    trap 'popd > /dev/null 2>&1 || true' RETURN

    timeout 30 git fetch origin "$branch" 2>&1 | tee -a "$LOG_FILE"

    local local_sha remote_sha
    local_sha=$(git rev-parse HEAD)
    remote_sha=$(git rev-parse "origin/$branch")

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
    timeout 30 git pull origin "$branch" 2>&1 | tee -a "$LOG_FILE"
    timeout 60 uv sync --all-extras --frozen 2>&1 | tee -a "$LOG_FILE"

    if [[ "$name" == "$PROJECT" ]]; then
        # Only run tests for the primary project repo.
        if [[ -n "${PROJECT_TEST_CMD:-}" ]]; then
            local -a test_cmd_arr
            read -r -a test_cmd_arr <<< "$PROJECT_TEST_CMD"
            if ! timeout 120 "${test_cmd_arr[@]}" 2>&1 | tee -a "$LOG_FILE"; then
                log "ERROR: $name tests failed for $remote_sha — rolling back and marking SHA as bad."
                echo "$remote_sha" >> "$FAIL_FILE"
                log "Capturing pre-reset state for forensics:"
                git diff HEAD 2>&1 | tee -a "$LOG_FILE" || true
                git status --short 2>&1 | tee -a "$LOG_FILE" || true
                git reset --keep "$local_sha" 2>&1 | tee -a "$LOG_FILE"
                uv sync --all-extras --frozen 2>&1 | tee -a "$LOG_FILE"
                exit 1
            fi
        fi
        # Clear fail log on success so it does not grow unbounded.
        rm -f "$FAIL_FILE"
    fi

    _REPO_UPDATED["$name"]=true

    # Run the caller-supplied upgrade hook (e.g. uv sync --upgrade-package voicecli).
    if [[ -n "$upgrade_hook" ]] && declare -f "$upgrade_hook" > /dev/null 2>&1; then
        log "$name: running upgrade hook: $upgrade_hook"
        "$upgrade_hook"
    fi
}

# _repo_was_updated <name> — returns 0 (true) if check_repo updated that repo.
_repo_was_updated() {
    [[ "${_REPO_UPDATED[${1:-}]:-false}" == "true" ]]
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

    pushd "$PROJECT_DIR" > /dev/null
    trap 'popd > /dev/null 2>&1 || true' RETURN

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
# Also reads from $HOME/.<PROJECT>/.image-digest as a belt-and-braces check.

restart_services() {
    _require_var IMAGE
    _require_var EXPECTED_IMAGE_ID

    # Re-read persisted digest as belt-and-braces verification.
    local digest_file="$HOME/.${PROJECT}/.image-digest"
    if [[ -f "$digest_file" ]]; then
        local persisted_id
        persisted_id=$(cat "$digest_file")
        if [[ "$persisted_id" != "$EXPECTED_IMAGE_ID" ]]; then
            log "ERROR: persisted image digest does not match EXPECTED_IMAGE_ID."
            log "  Persisted: $persisted_id"
            log "  Expected:  $EXPECTED_IMAGE_ID"
            log "  Re-run build_image or clear ${digest_file}."
            exit 1
        fi
    fi

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

    local -a adapter_services_arr hub_services_arr all_services_arr
    read -r -a adapter_services_arr <<< "${ADAPTER_SERVICES:-}"
    [[ -n "${HUB_SERVICE:-}" ]] && hub_services_arr=("$HUB_SERVICE") || hub_services_arr=()
    all_services_arr=("${hub_services_arr[@]+"${hub_services_arr[@]}"}" "${adapter_services_arr[@]+"${adapter_services_arr[@]}"}")

    if [[ ${#all_services_arr[@]} -eq 0 ]]; then
        log "WARN: no services configured (HUB_SERVICE + ADAPTER_SERVICES both empty); skipping restart"
        return 0
    fi

    log "Removing old containers to force new image pickup..."
    podman rm -f "${all_services_arr[@]}" 2>/dev/null || true

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
            if [[ ${#adapter_services_arr[@]} -gt 0 ]]; then
                systemctl --user stop "${adapter_services_arr[@]}" 2>&1 | tee -a "$LOG_FILE"
            fi
            exit 1
        fi
    fi

    if [[ ${#adapter_services_arr[@]} -gt 0 ]]; then
        log "Restarting ${ADAPTER_SERVICES}..."
        systemctl --user restart "${adapter_services_arr[@]}" 2>&1 | tee -a "$LOG_FILE"
    fi

    # Verify all services reached active.
    log "Verifying services..."
    local -a all_units_arr=()
    [[ -n "${HUB_SERVICE:-}" ]] && all_units_arr+=("${HUB_SERVICE}.service")
    local svc
    for svc in "${adapter_services_arr[@]+"${adapter_services_arr[@]}"}"; do
        all_units_arr+=("${svc}.service")
    done

    local healthy=false
    local j=1
    while [[ "$j" -le 12 ]]; do
        sleep 5
        local failed=0
        local unit
        for unit in "${all_units_arr[@]}"; do
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
        log "Waiting for services... ($failed not active, attempt $j/12)"
        j=$(( j + 1 ))
    done

    if [[ "$healthy" == false ]]; then
        log "ERROR: Some services failed to reach active after 60s:"
        systemctl --user status "${all_units_arr[@]}" --no-pager --lines=0 2>&1 | tee -a "$LOG_FILE"
        exit 1
    fi
}

# ── run_deploy ────────────────────────────────────────────────────────────────
# Top-level orchestrator. Call after setting all required variables.
#
# Flow:
#   1. verify_env_files (pre-flight)
#   2. check_repo for PROJECT_DIR
#   3. check_repo for each entry in EXTRA_REPOS
#   4. Early-exit if nothing updated
#   5. build_image
#   6. restart_services (includes verify_env_files again as defense in depth)
#   7. Log tags summary

run_deploy() {
    _require_var PROJECT
    _require_var PROJECT_DIR
    _require_var IMAGE
    _require_var LOG_FILE
    _require_var FAIL_FILE

    mkdir -p "$(dirname "$LOG_FILE")"

    # Pre-flight env-file check before any git operations.
    verify_env_files

    # Check primary project repo (always; runs tests if PROJECT_TEST_CMD is set).
    check_repo "$PROJECT" "$PROJECT_DIR" ""

    # Check extra repos (no tests; caller supplies upgrade hook).
    # EXTRA_REPOS is newline-separated; each line: "name:path:hook"
    local entry name path hook
    while IFS= read -r entry; do
        [[ -z "$entry" ]] && continue
        name="${entry%%:*}"
        local rest="${entry#*:}"
        path="${rest%%:*}"
        hook=""
        [[ "$rest" == *:* ]] && hook="${rest#*:}"
        [[ -z "$path" ]] && { log "WARN: EXTRA_REPOS entry '$entry' has empty path — skipping"; continue; }
        check_repo "$name" "$path" "${hook:-}"
    done <<< "${EXTRA_REPOS:-}"

    # Early-exit if nothing changed.
    local any_updated=false
    if _repo_was_updated "$PROJECT"; then
        any_updated=true
    fi
    while IFS= read -r entry; do
        [[ -z "$entry" ]] && continue
        name="${entry%%:*}"
        if _repo_was_updated "$name"; then
            any_updated=true
        fi
    done <<< "${EXTRA_REPOS:-}"

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
    while IFS= read -r entry; do
        [[ -z "$entry" ]] && continue
        name="${entry%%:*}"
        local rest2="${entry#*:}"
        path="${rest2%%:*}"
        if _repo_was_updated "$name"; then
            tags="${tags} ${name}=$(cd "$path" && git rev-parse --short HEAD)"
        fi
    done <<< "${EXTRA_REPOS:-}"
    log "Deploy complete:${tags}"
}
