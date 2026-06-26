#!/usr/bin/env bash
# deploy/lib/operator-log.sh — append-only operator audit (JSONL).
#
# Usage (after source):
#   op_log <event> [key=value ...]
#   rotation_log_append <secret> <reason> [trigger]
#
# Never pass secret material (tokens, seeds, env values) in key=value pairs.

# shellcheck shell=bash

OPERATOR_LOG="${OPERATOR_LOG:-${XDG_STATE_HOME:-$HOME/.local/state}/factory/logs/operator.log}"
ROTATION_LOG="${ROTATION_LOG:-$HOME/.roxabi/factory/rotation-log.md}"

_op_json_string() {
    local s="${1-}"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\r'/\\r}"
    s="${s//$'\t'/\\t}"
    printf '%s' "$s"
}

_op_operator_user() {
    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
        printf '%s' "${SUDO_USER}"
        return
    fi
    printf '%s' "${LOGNAME:-$(id -un 2>/dev/null || echo unknown)}"
}

# Append one JSON object per line. Fail-soft — logging must not break deploy.
op_log() {
    local event="${1:?op_log: event required}"
    shift
    local ts user host pid line extra k v
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u)"
    user="$(_op_operator_user)"
    host="$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo unknown)"
    pid="$$"
    extra=""
    while [[ $# -gt 0 ]]; do
        k="${1%%=*}"
        v="${1#*=}"
        if [[ "$k" == "$1" ]]; then
            shift
            continue
        fi
        if [[ -n "$extra" ]]; then
            extra+=","
        fi
        extra+="\"$(_op_json_string "$k")\":\"$(_op_json_string "$v")\""
        shift
    done
    line="{\"ts\":\"$(_op_json_string "$ts")\",\"user\":\"$(_op_json_string "$user")\",\"host\":\"$(_op_json_string "$host")\",\"pid\":${pid},\"event\":\"$(_op_json_string "$event")\""
    if [[ -n "$extra" ]]; then
        line+=",${extra}"
    fi
    line+="}"
    (
        umask 0077
        mkdir -p "$(dirname "${OPERATOR_LOG}")" 2>/dev/null || exit 0
        printf '%s\n' "$line" >> "${OPERATOR_LOG}" 2>/dev/null || true
    ) || true
}

# Human-readable credential rotation record (Syncthing-synced under ~/.roxabi/factory/).
rotation_log_append() {
    local secret="${1:?rotation_log_append: secret required}"
    local reason="${2:?rotation_log_append: reason required}"
    local trigger="${3:-manual}"
    local ts user host line rot_dir
    ts="$(date -u +%Y-%m-%d 2>/dev/null || date -u +%Y-%m-%d)"
    user="$(_op_operator_user)"
    host="$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo unknown)"
    line="${ts} | secret:${secret} | reason:${reason} | by:${user} | trigger:${trigger} | host:${host}"
    rot_dir="$(dirname "${ROTATION_LOG}")"
    (
        umask 0077
        mkdir -p "${rot_dir}" 2>/dev/null || exit 0
        if [[ ! -f "${ROTATION_LOG}" ]]; then
            printf '# Factory credential rotation log\n\n' >> "${ROTATION_LOG}" 2>/dev/null || true
        fi
        printf '%s\n' "$line" >> "${ROTATION_LOG}" 2>/dev/null || true
    ) || true
    op_log rotation_log secret="${secret}" reason="${reason}" trigger="${trigger}"
}