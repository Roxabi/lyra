#!/usr/bin/env bash
# Bootstrap otel-raw directories for collector JSONL + dashboard SQLite index (ADR-097).
set -euo pipefail

OTEL_DIR="${HOME}/.local/state/factory/otel"
ROXABI_DIR="${HOME}/.roxabi/factory"

mkdir -p "${OTEL_DIR}" "${ROXABI_DIR}"
chmod 750 "${OTEL_DIR}" "${ROXABI_DIR}"
touch "${ROXABI_DIR}/otel-raw.db"
chmod 640 "${ROXABI_DIR}/otel-raw.db"

echo "otel-raw bootstrap ok:"
echo "  JSONL dir: ${OTEL_DIR}"
echo "  SQLite:    ${ROXABI_DIR}/otel-raw.db (dashboard RW bind mount)"