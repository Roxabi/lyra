"""Configuration dataclass for tool-call display in streaming responses.

Loaded by bootstrap from the ``[tool_display]`` section of config.toml.
When the section is absent, ``ToolDisplayConfig.defaults()`` is used.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field, fields
from types import MappingProxyType

# Canonical show-key names (lowercase).  StreamProcessor normalises tool_name
# to lowercase before lookup.
_DEFAULT_SHOW: dict[str, bool] = {
    "edit": True,
    "write": True,
    "bash": True,
    "web_fetch": True,
    "web_search": True,
    "agent": True,
    "read": False,
    "grep": False,
    "glob": False,
}


@dataclass(frozen=True)
class ToolDisplayConfig:
    """Immutable configuration controlling how tool calls are rendered during streaming.

    Attributes
    ----------
    names_threshold:
        Number of individual file-edit names to show per file before switching
        to count-only mode (e.g. "3 edits").  Default: 3.
    group_threshold:
        Number of distinct files before switching from per-file display to a
        grouped summary (e.g. "4 files edited").  Default: 3.
    bash_max_len:
        Maximum characters to display per bash command before truncating.
        Default: 60.
    throttle_ms:
        Minimum milliseconds between consecutive ``ToolSummaryRenderEvent``
        emissions during a single turn.  The final ``is_complete=True`` emission
        always bypasses this throttle.  Default: 2000.  Use 0 to disable
        throttling entirely.
    show:
        Read-only mapping of tool name → whether to surface the call in the
        summary card.  Keys not present in this map are treated as ``False``
        (silent).  Mutation raises ``TypeError``.
    """

    names_threshold: int = 3
    group_threshold: int = 3
    bash_max_len: int = 60
    throttle_ms: int = 2000
    show: MappingProxyType[str, bool] = field(
        default_factory=lambda: MappingProxyType(dict(_DEFAULT_SHOW))
    )

    def __post_init__(self) -> None:
        """Validate numeric fields on construction.

        Raises
        ------
        ValueError
            If any numeric field is out of its acceptable range.
        """
        if self.names_threshold < 1:
            raise ValueError(
                f"names_threshold must be >= 1, got {self.names_threshold}"
            )
        if self.group_threshold < 1:
            raise ValueError(
                f"group_threshold must be >= 1, got {self.group_threshold}"
            )
        if self.bash_max_len < 1:
            raise ValueError(f"bash_max_len must be >= 1, got {self.bash_max_len}")
        if self.throttle_ms < 0:
            raise ValueError(f"throttle_ms must be >= 0, got {self.throttle_ms}")

    @classmethod
    def defaults(cls) -> ToolDisplayConfig:
        """Return a ``ToolDisplayConfig`` populated with hardcoded defaults.

        Used as the interim config when the ``[tool_display]`` section is absent
        from ``config.toml``, and during S4 integration before S5 is merged.
        """
        return cls()

    @classmethod
    def from_dict(cls, data: dict) -> ToolDisplayConfig:
        """Build a ``ToolDisplayConfig`` from a raw TOML section dict.

        Only recognised keys are used; unknown keys are silently ignored.
        ``[tool_display.show]`` sub-table is merged with the defaults so that
        any keys omitted in config.toml keep their default values.

        Args:
            data: Raw dict from the ``[tool_display]`` TOML section.
                  An empty dict returns ``ToolDisplayConfig.defaults()``.

        Raises:
            ValueError: If a numeric field value cannot be coerced to ``int``,
                        or if a numeric field is out of its valid range.
        """
        if not data:
            return cls.defaults()

        # Use the authoritative dataclasses API rather than relying on CPython's
        # implementation detail of scalar field defaults being set as class attrs.
        _field_defaults = {
            f.name: f.default
            for f in fields(cls)
            if f.default is not dataclasses.MISSING
        }

        show_overrides: dict[str, bool] = data.get("show", {})
        merged_show = MappingProxyType({**_DEFAULT_SHOW, **show_overrides})

        return cls(
            names_threshold=int(
                data.get("names_threshold", _field_defaults["names_threshold"])
            ),
            group_threshold=int(
                data.get("group_threshold", _field_defaults["group_threshold"])
            ),
            bash_max_len=int(data.get("bash_max_len", _field_defaults["bash_max_len"])),
            throttle_ms=int(data.get("throttle_ms", _field_defaults["throttle_ms"])),
            show=merged_show,
        )
