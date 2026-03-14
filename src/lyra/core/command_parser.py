from __future__ import annotations

from dataclasses import dataclass

COMMAND_PREFIXES = ("/", "!")


@dataclass(frozen=True)
class CommandContext:
    prefix: str  # "/" or "!"
    name: str  # lowercased command name, no prefix
    args: str  # remainder after name, stripped
    raw: str  # original full text


class CommandParser:
    """Stateless parser for / and ! command prefixes."""

    def parse(self, text: str) -> CommandContext | None:
        for prefix in COMMAND_PREFIXES:
            if text.startswith(prefix) and len(text) > len(prefix):
                remainder = text[len(prefix) :]
                if remainder and not remainder[0].isspace():
                    parts = remainder.split(None, 1)
                    return CommandContext(
                        prefix=prefix,
                        name=parts[0].lower(),
                        args=parts[1] if len(parts) > 1 else "",
                        raw=text,
                    )
        return None
