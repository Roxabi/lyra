#!/usr/bin/env bash
# Post-worktree-create hook invoked by dev-core /implement.
# Symlinks the main repo's .venv into the new worktree so Pyright/uv resolve
# third-party imports immediately. Branches share uv.lock, so this is safe;
# if a branch bumps deps, `rm .venv && uv sync` inside the worktree.
set -euo pipefail

MAIN_REPO=$(git worktree list --porcelain | awk '/^worktree / {print $2; exit}')

if [ -z "${MAIN_REPO:-}" ] || [ ! -d "${MAIN_REPO}/.venv" ]; then
  echo "worktree-setup: main repo .venv not found at ${MAIN_REPO:-?} — skipping" >&2
  exit 0
fi

if [ "${PWD}" = "${MAIN_REPO}" ]; then
  echo "worktree-setup: running inside main repo, refusing to symlink .venv onto itself" >&2
  exit 0
fi

if [ -L .venv ]; then
  rm .venv
elif [ -d .venv ]; then
  echo "worktree-setup: .venv already exists as a real directory — leaving it untouched" >&2
  exit 0
fi

ln -s "${MAIN_REPO}/.venv" .venv
echo "worktree-setup: linked .venv → ${MAIN_REPO}/.venv"
