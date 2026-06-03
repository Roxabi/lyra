"""Startup git ownership probe — fails fast if uid mapping is broken."""

from __future__ import annotations

import logging
import os
import subprocess
import sys

log = logging.getLogger(__name__)

DEFAULT_PROBE_PATH = "/home/factory/projects/roxabi-factory"
PROBE_ENV_VAR = "FACTORY_OWNERSHIP_PROBE_PATH"


def run_git_ownership_probe(repo_path: str | None = None) -> None:
    """Run `git rev-parse HEAD` on a known bind-mounted repo.

    Path resolution order:
      1. `repo_path` argument
      2. env var FACTORY_OWNERSHIP_PROBE_PATH
      3. default "/home/factory/projects/roxabi-factory"

    On non-zero exit OR stderr containing "dubious ownership":
      log.error(verbatim stderr); sys.exit(1)
    Missing target dir → log.error + sys.exit(1) (same loud-regression behavior).
    On success: log.info("clipool git ownership probe OK (target=<path>)")
    """
    target = repo_path or os.environ.get(PROBE_ENV_VAR) or DEFAULT_PROBE_PATH

    if os.path.islink(target) and not os.path.isdir(target):
        # Symlink exists but its target does not resolve to a directory — the most
        # common cause in container deployments is an absolute bridge symlink whose
        # target path is valid on the host but absent inside the container mount-ns.
        # Absolute targets dangle across container mount-namespaces; relative targets
        # survive because they are resolved relative to the symlink's own directory.
        log.error(
            "git ownership probe: cross-namespace bridge symlink must be RELATIVE"
            " — absolute target dangles across the container mount-ns"
            " (target=%s, link=%s); fix: re-create symlink with a relative target"
            " (e.g. ln -sfr <src> <link>)",
            os.readlink(target),
            target,
        )
        sys.exit(1)

    if not os.path.isdir(target):
        log.error(
            "git ownership probe: target directory does not exist (target=%s)", target
        )
        sys.exit(1)

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            cwd=target,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        log.error(
            "git ownership probe timed out after %ds (target=%s)",
            5,
            target,
        )
        sys.exit(1)
    except FileNotFoundError:
        log.error("git ownership probe: git binary not found on PATH")
        sys.exit(1)

    if result.returncode != 0 or "dubious ownership" in result.stderr:
        log.error(
            "git ownership probe failed (target=%s):\n%s",
            target,
            result.stderr,
        )
        sys.exit(1)

    log.info("clipool git ownership probe OK (target=%s)", target)
