"""Configuration dataclass for tool-call display in streaming responses.

Loaded by bootstrap from the ``[tool_display]`` section of config.toml.
When the section is absent, ``ToolDisplayConfig.defaults()`` is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
        always bypasses this throttle.  Default: 2000.
    show:
        Mapping of tool name → whether to surface the call in the summary card.
        Keys not present in this map are treated as ``False`` (silent).
    """

    names_threshold: int = 3
    group_threshold: int = 3
    bash_max_len: int = 60
    throttle_ms: int = 2000
    show: dict[str, bool] = field(default_factory=lambda: dict(_DEFAULT_SHOW))

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
        """
        if not data:
            return cls.defaults()

        show_overrides: dict[str, bool] = data.get("show", {})
        merged_show = {**_DEFAULT_SHOW, **show_overrides}

        return cls(
            names_threshold=int(data.get("names_threshold", cls.names_threshold)),
            group_threshold=int(data.get("group_threshold", cls.group_threshold)),
            bash_max_len=int(data.get("bash_max_len", cls.bash_max_len)),
            throttle_ms=int(data.get("throttle_ms", cls.throttle_ms)),
            show=merged_show,
        )
