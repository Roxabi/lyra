"""Plugin loader for Lyra hub (issue #106).

Discovers TOML manifests in the plugins directory, loads async Python handler
modules, and provides command dispatch to CommandRouter.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import sys
import tomllib
from collections.abc import Awaitable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Callable, cast

if TYPE_CHECKING:
    from lyra.core.message import InboundMessage, Response
    from lyra.core.pool import Pool

log = logging.getLogger(__name__)

AsyncHandler = Callable[["InboundMessage", "Pool", list[str]], "Awaitable[Response]"]


@dataclass(frozen=True)
class CommandSpec:
    """A single command declared in a plugin manifest."""

    name: str
    description: str = ""
    handler: str = ""


@dataclass(frozen=True)
class PluginManifest:
    """Parsed from plugin.toml."""

    name: str
    description: str = ""
    version: str = "0.1.0"
    priority: int = 100
    enabled: bool = True
    timeout: float = 30.0
    commands: tuple[CommandSpec, ...] = field(default=())


@dataclass
class LoadedPlugin:
    """A loaded plugin with resolved handler callables."""

    name: str
    manifest: PluginManifest
    module: ModuleType
    handlers: dict[str, AsyncHandler] = field(default_factory=dict)


def _parse_manifest(data: dict) -> PluginManifest:
    """Parse a dict from tomllib.load() into a PluginManifest."""
    commands = tuple(
        CommandSpec(
            name=cmd["name"],
            description=cmd.get("description", ""),
            handler=cmd.get("handler", ""),
        )
        for cmd in data.get("commands", [])
    )
    return PluginManifest(
        name=data["name"],
        description=data.get("description", ""),
        version=data.get("version", "0.1.0"),
        priority=int(data.get("priority", 100)),
        enabled=bool(data.get("enabled", True)),
        timeout=float(data.get("timeout", 30.0)),
        commands=commands,
    )


class PluginLoader:
    """Discovers and loads directory-based plugins with TOML manifests."""

    def __init__(self, plugins_dir: Path) -> None:
        self.plugins_dir = plugins_dir
        self._loaded: dict[str, LoadedPlugin] = {}

    def discover(self) -> list[PluginManifest]:
        """Scan plugins_dir and return manifests for all valid plugin directories.

        Silently skips directories without plugin.toml or with malformed manifests.
        """
        manifests: list[PluginManifest] = []
        if not self.plugins_dir.is_dir():
            return manifests
        for subdir in sorted(self.plugins_dir.iterdir()):
            if not subdir.is_dir():
                continue
            toml_path = subdir / "plugin.toml"
            if not toml_path.exists():
                continue
            try:
                with toml_path.open("rb") as f:
                    data = tomllib.load(f)
            except Exception:  # noqa: BLE001  # resilient: skip unreadable plugin.toml
                log.debug("Skipping malformed plugin.toml in %s", subdir)
                continue
            try:
                manifests.append(_parse_manifest(data))
            except (KeyError, TypeError, ValueError) as e:
                log.debug("Skipping invalid manifest in %s: %s", subdir, e)
                continue
        return manifests

    def _validate_name(self, name: str) -> None:
        """Validate plugin name: safe characters + no path traversal."""
        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            raise ValueError(
                f"Invalid plugin name {name!r}: only [a-zA-Z0-9_-] allowed"
            )
        plugin_dir = self.plugins_dir / name
        if not plugin_dir.resolve().is_relative_to(self.plugins_dir.resolve()):
            raise ValueError(f"Plugin name {name!r} escapes plugins directory")

    def load(self, name: str) -> LoadedPlugin:
        """Load a plugin by name. Raises ValueError if a handler is missing."""
        self._validate_name(name)
        plugin_dir = self.plugins_dir / name
        toml_path = plugin_dir / "plugin.toml"
        handlers_path = plugin_dir / "handlers.py"

        with toml_path.open("rb") as f:
            data = tomllib.load(f)
        manifest = _parse_manifest(data)

        spec = importlib.util.spec_from_file_location(
            f"lyra.plugins.{name}.handlers", handlers_path
        )
        if spec is None or spec.loader is None:
            raise ValueError(
                f"Cannot load plugin '{name}': "
                f"importlib spec is None for {handlers_path}"
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"lyra.plugins.{name}.handlers"] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]  # guarded by None check above

        handlers: dict[str, AsyncHandler] = {}
        for cmd in manifest.commands:
            fn = getattr(module, cmd.handler, None)
            if fn is None or not callable(fn):
                raise ValueError(
                    f"Plugin '{name}': handler '{cmd.handler}'"
                    " not found or not callable"
                )
            handlers[f"/{cmd.name}"] = cast("AsyncHandler", fn)

        loaded = LoadedPlugin(
            name=name, manifest=manifest, module=module, handlers=handlers
        )
        self._loaded[name] = loaded
        return loaded

    def reload(self, name: str) -> LoadedPlugin:
        """Reload a plugin (re-reads manifest + reimports module)."""
        self._validate_name(name)
        if name not in self._loaded:
            return self.load(name)
        existing = self._loaded[name]
        plugin_dir = self.plugins_dir / name

        with (plugin_dir / "plugin.toml").open("rb") as f:
            data = tomllib.load(f)
        manifest = _parse_manifest(data)

        # Re-execute module source in the existing module object so handler
        # callables are refreshed without requiring sys.modules parent chain.
        handlers_path = plugin_dir / "handlers.py"
        spec = importlib.util.spec_from_file_location(
            f"lyra.plugins.{name}.handlers", handlers_path
        )
        if spec is None or spec.loader is None:
            raise ValueError(
                f"Cannot reload plugin '{name}': "
                f"importlib spec is None for {handlers_path}"
            )
        spec.loader.exec_module(existing.module)  # type: ignore[union-attr]

        handlers: dict[str, AsyncHandler] = {}
        for cmd in manifest.commands:
            fn = getattr(existing.module, cmd.handler, None)
            if fn is None or not callable(fn):
                raise ValueError(
                    f"Plugin '{name}': handler '{cmd.handler}' not found after reload"
                )
            handlers[f"/{cmd.name}"] = cast("AsyncHandler", fn)

        loaded = LoadedPlugin(
            name=name, manifest=manifest, module=existing.module, handlers=handlers
        )
        self._loaded[name] = loaded
        return loaded

    def get_commands(self, enabled: list[str]) -> dict[str, AsyncHandler]:
        """Return handler dict for all loaded plugins in the enabled list."""
        result: dict[str, AsyncHandler] = {}
        for name, plugin in self._loaded.items():
            if name in enabled:
                result.update(plugin.handlers)
        return result

    def get_timeout(self, command_name: str, enabled: list[str]) -> float:
        """Return the timeout for a plugin command (default 30s)."""
        for name, plugin in self._loaded.items():
            if name in enabled and command_name in plugin.handlers:
                return plugin.manifest.timeout
        return 30.0
