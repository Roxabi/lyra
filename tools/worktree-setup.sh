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
  echo "worktree-setup: linked .venv → ${main_repo}/.venv" >&2
}

# ccc resolves the project to the shared main-repo root, so a single incremental
# index (~1.6s) keeps semantic search current for the new worktree. ccc is an
# optional, user-local tool; a missing binary, a failed index, or a slow cold
# rebuild must never block worktree creation, so all cases are non-fatal and the
# run is bounded by a timeout.
refresh_ccc_index() {
  if ! command -v ccc >/dev/null 2>&1; then
    echo "worktree-setup: ccc not found — skipping index refresh" >&2
    return 0
  fi

  # Capture output so a failure is debuggable without a re-run; bound the run so
  # a cold/corrupted index can't stall the hook indefinitely (timeout exits
  # non-zero → handled by the same non-fatal else branch).
  local ccc_out
  if ccc_out=$(timeout 30 ccc index 2>&1); then
    echo "worktree-setup: refreshed ccc index" >&2
  else
    echo "worktree-setup: ccc index failed — continuing" >&2
    echo "${ccc_out}" >&2
  fi
}

link_venv
refresh_ccc_index
