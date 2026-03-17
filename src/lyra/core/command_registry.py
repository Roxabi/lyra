"""Shared command registry for platform-native command menus (#291).

Provides PlatformCommand/CommandParam dataclasses and collect_commands()
to merge commands from all sources into a single list for Telegram
setMyCommands and Discord app_commands registration.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CommandParam:
    """Typed parameter for a platform command (Discord app_commands)."""

    name: str
    description: str = ""
    required: bool = False
    choices: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PlatformCommand:
    """A command registerable with platform-native menus."""

    name: str
    description: str = ""
    params: list[CommandParam] = field(default_factory=list)
    admin_only: bool = False


def collect_commands(
    builtin_metadata: list[tuple[str, str, bool]],
    plugin_descriptions: dict[str, str],
    voice_commands: list[PlatformCommand],
) -> list[PlatformCommand]:
    """Merge all command sources into a unified PlatformCommand list.

    Args:
        builtin_metadata: (name, description, admin_only) from CommandRouter.
        plugin_descriptions: {'/cmd': 'description'} from PluginLoader.
        voice_commands: PlatformCommand list from discord_voice_commands.

    Returns:
        Sorted, deduplicated list of PlatformCommand.
    """
    commands: list[PlatformCommand] = []
    seen: set[str] = set()
    for name, desc, admin in builtin_metadata:
        commands.append(PlatformCommand(name=name, description=desc, admin_only=admin))
        seen.add(name)
    for name, desc in plugin_descriptions.items():
        if name not in seen:
            commands.append(PlatformCommand(name=name, description=desc))
            seen.add(name)
    for vc in voice_commands:
        if vc.name not in seen:
            commands.append(vc)
            seen.add(vc.name)
    return sorted(commands, key=lambda c: c.name)
