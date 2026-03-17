"""Command reload manager for AgentBase (extracted from agent.py).

Encapsulates plugin discovery, loading, mtime tracking, and hot-reload
detection. AgentBase delegates all command lifecycle to this class.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_config import Agent
    from .command_loader import CommandLoader

log = logging.getLogger(__name__)


def _file_sha256(path: Path) -> str:
    """Return the hex SHA-256 digest of *path*, or empty string on read error."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


class CommandReloadManager:
    """Owns plugin lifecycle: discovery, loading, mtime tracking, hot-reload."""

    def __init__(
        self, config: Agent, command_loader: CommandLoader, plugins_dir: Path
    ) -> None:
        self._config = config
        self._command_loader = command_loader
        self._plugins_dir = plugins_dir
        self.effective_plugins: list[str] = self._init_plugins()
        self.plugin_mtimes: dict[str, float] = self._record_plugin_mtimes()
        self.plugin_hashes: dict[str, str] = self._record_plugin_hashes()

    def _init_plugins(self) -> list[str]:
        """Load plugins and return the effective enabled list.

        Only plugins that load successfully are included in the returned list.
        If a plugin has enabled=false in its manifest, load() raises ValueError
        and the plugin is skipped -- this enforces SC-9 regardless of agent config.
        """
        if self._config.commands_enabled:
            names = list(self._config.commands_enabled)
        else:
            # default-open: load all manifest.enabled=True plugins discovered in
            # plugins_dir. Security assumption: plugins_dir is a trusted directory
            # controlled by the operator. Do not point plugins_dir at a
            # world-writable or network-accessible path.
            manifests = self._command_loader.discover()
            names = [m.name for m in manifests if m.enabled]
        effective: list[str] = []
        for name in names:
            try:
                self._command_loader.load(name)
                effective.append(name)
            except ValueError as exc:
                log.warning("Skipping plugin %r: %s", name, exc)
            except Exception:  # noqa: BLE001  # resilient: don't let one bad plugin block startup
                log.warning("Failed to load plugin %r", name, exc_info=True)
        return effective

    def _record_plugin_mtimes(self) -> dict[str, float]:
        """Record current mtime for each loaded plugin's handlers.py."""
        mtimes: dict[str, float] = {}
        for name in self.effective_plugins:
            handlers_path = self._plugins_dir / name / "handlers.py"
            try:
                mtimes[name] = handlers_path.stat().st_mtime
            except OSError:
                pass
        return mtimes

    def _record_plugin_hashes(self) -> dict[str, str]:
        """Record SHA-256 hash of each loaded plugin's handlers.py."""
        hashes: dict[str, str] = {}
        for name in self.effective_plugins:
            h = _file_sha256(self._plugins_dir / name / "handlers.py")
            if h:
                hashes[name] = h
        return hashes

    def reload_plugins(self) -> bool:
        """Hot-reload changed plugins. Return True if any plugin was reloaded.

        Uses mtime as a cheap first check, then verifies SHA-256 hash before
        executing the reload. This prevents forged-mtime attacks (M-11).
        """
        changed = False
        for name in list(self.plugin_mtimes):
            handlers_path = self._plugins_dir / name / "handlers.py"
            try:
                new_mtime = handlers_path.stat().st_mtime
            except OSError:
                continue
            if new_mtime <= self.plugin_mtimes[name]:
                continue
            new_hash = _file_sha256(handlers_path)
            if not new_hash or new_hash == self.plugin_hashes.get(name):
                # mtime changed but content is identical — skip reload
                self.plugin_mtimes[name] = new_mtime
                continue
            try:
                self._command_loader.reload(name)
                self.plugin_mtimes[name] = new_mtime
                self.plugin_hashes[name] = new_hash
                changed = True
                log.info("Hot-reloaded plugin %r (hash changed)", name)
            except Exception:  # noqa: BLE001  # resilient: don't let hot-reload crash the agent
                log.warning("Failed to reload plugin %r", name, exc_info=True)
        return changed
