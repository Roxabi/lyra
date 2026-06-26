#!/usr/bin/env bash
# deploy/lib/syncthing-ignore.sh — idempotent ~/.roxabi/factory/.stignore maintenance.
#
# Usage (after source):
#   ensure_factory_stignore [template_path]

# shellcheck shell=bash

ensure_factory_stignore() {
    local template="${1:-}"
    local stignore="${HOME}/.roxabi/factory/.stignore"
    local line

    if [[ -z "${template}" ]]; then
        echo "ensure_factory_stignore: template path required" >&2
        return 1
    fi
    if [[ ! -f "${template}" ]]; then
        echo "ensure_factory_stignore: template not found: ${template}" >&2
        return 1
    fi

    mkdir -p "$(dirname "${stignore}")" 2>/dev/null || return 0

    if [[ ! -f "${stignore}" ]]; then
        cp "${template}" "${stignore}" 2>/dev/null || return 0
        chmod 0644 "${stignore}" 2>/dev/null || true
        return 0
    fi

    while IFS= read -r line || [[ -n "${line}" ]]; do
        [[ -z "${line}" || "${line}" =~ ^[[:space:]]*# ]] && continue
        if ! grep -qxF "${line}" "${stignore}" 2>/dev/null; then
            printf '%s\n' "${line}" >> "${stignore}" 2>/dev/null || true
        fi
    done < "${template}"
}