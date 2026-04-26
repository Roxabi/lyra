"""Hub lockfile management — PID-based singleton enforcement."""

from __future__ import annotations

import atexit
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def lockfile_path() -> Path:
    """Resolve the hub lockfile path from LYRA_VAULT_DIR at call time."""
    return (
        Path(os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra"))).resolve()
        / "hub.lock"
    )


def release_lockfile() -> None:
    """Remove the Hub lockfile if it exists."""
    lf = lockfile_path()
    try:
        lf.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Could not remove lockfile %s: %s", lf, exc)


def acquire_lockfile() -> None:
    """Write current PID to the lockfile.

    If the lockfile already exists and the recorded PID is still alive,
    log an error and exit — another Hub process is running.
    Registers an atexit handler to clean up the lockfile on normal exit.
    """
    lf = lockfile_path()
    if lf.exists():
        try:
            pid_str = lf.read_text().strip()
            pid = int(pid_str)
            try:
                os.kill(pid, 0)  # signal 0 = existence check only
                # In a container the hub runs as PID 1; on OOM-kill the atexit
                # handler is skipped, so the lock persists. The next container
                # restart also gets PID 1, making the stale PID appear alive.
                if pid == os.getpid():
                    log.warning(
                        "Stale lockfile found (PID %d matches our PID"
                        " — container restart?) — overwriting",
                        pid,
                    )
                else:
                    sys.exit(
                        f"Hub is already running (PID {pid}). "
                        f"Remove {lf} if the process is stale."
                    )
            except ProcessLookupError:
                # PID no longer alive — stale lockfile, safe to overwrite
                log.warning(
                    "Stale lockfile found (PID %d not running) — overwriting", pid
                )
            except PermissionError:
                # PID exists but we can't signal it — treat as alive
                sys.exit(
                    f"Hub is already running (PID {pid}, permission denied). "
                    f"Remove {lf} if the process is stale."
                )
        except (ValueError, OSError) as exc:
            log.warning("Could not read lockfile %s (%s) — overwriting", lf, exc)

    lf.parent.mkdir(parents=True, exist_ok=True)
    lf.write_text(str(os.getpid()))
    atexit.register(release_lockfile)
