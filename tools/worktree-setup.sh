#!/usr/bin/env bash
# Post-worktree-create hook invoked by dev-core /implement.
# 1. Symlinks the main repo's .venv into the new worktree so Pyright/uv resolve
#    third-party imports immediately. Branches share uv.lock, so this is safe;
#    if a branch bumps deps, `rm .venv && uv sync` inside the worktree.
# 2. Refreshes the cocoindex-code (ccc) index so retrieval-ladder step-3
#    (semantic search) reflects the current tree in the new worktree.
set -euo pipefail

link_venv() {
  local main_repo
  main_repo=$(git worktree list --porcelain | awk '/^worktree / {print $2; exit}')

  if [ -z "${main_repo:-}" ] || [ ! -d "${main_repo}/.venv" ]; then
    echo "worktree-setup: main repo .venv not found at ${main_repo:-?} — skipping" >&2
    return 0
  fi

  if [ "${PWD}" = "${main_repo}" ]; then
    echo "worktree-setup: running inside main repo, refusing to symlink .venv onto itself" >&2
    return 0
  fi

  if [ -L .venv ]; then
    rm .venv
  elif [ -d .venv ]; then
    echo "worktree-setup: .venv already exists as a real directory — leaving it untouched" >&2
    return 0
  fi

  ln -s "${main_repo}/.venv" .venv
  echo "worktree-setup: linked .venv → ${main_repo}/.venv"
}

# ccc resolves the project to the shared main-repo root, so a single incremental
# index keeps semantic search current for the new worktree. ccc is an optional,
# user-local tool; a missing binary or a failed index must never block worktree
# creation, so both cases are non-fatal.
refresh_ccc_index() {
  if ! command -v ccc >/dev/null 2>&1; then
    echo "worktree-setup: ccc not found — skipping index refresh" >&2
    return 0
  fi

  if ccc index >/dev/null 2>&1; then
    echo "worktree-setup: refreshed ccc index"
  else
    echo "worktree-setup: ccc index failed — continuing" >&2
  fi
}

link_venv
refresh_ccc_index
