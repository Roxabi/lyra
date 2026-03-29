"""Configuration model for tool-call display in streaming responses.

Loaded by bootstrap from the ``[tool_display]`` section of config.toml.
When the section is absent, ``ToolDisplayConfig()`` is used (all defaults).
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

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


class ToolDisplayConfig(BaseModel):
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

    model_config = ConfigDict(frozen=True, extra="ignore")

    names_threshold: int = 3
    group_threshold: int = 3
    bash_max_len: int = 60
    throttle_ms: int = 2000
    # Stored as dict[str, bool] for Pydantic compatibility; exposed as
    # MappingProxyType via the .show property to preserve read-only semantics.
    _show: dict[str, bool] = {}

    def __init__(self, **data: Any) -> None:
        show_raw: Any = data.pop("show", None)
        super().__init__(**data)
        if show_raw is None:
            merged = dict(_DEFAULT_SHOW)
        elif isinstance(show_raw, MappingProxyType):
            merged = dict(show_raw)
        else:
            overrides: dict[str, bool] = {k: bool(v) for k, v in show_raw.items()}
            merged = {**_DEFAULT_SHOW, **overrides}
        object.__setattr__(self, "_show", merged)

    @property
    def show(self) -> MappingProxyType[str, bool]:
        """Read-only view of the tool-name → visibility map."""
        return MappingProxyType(self._show)

    @field_validator("names_threshold")
    @classmethod
    def _validate_names_threshold(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"names_threshold must be >= 1, got {v}")
        return v

    @field_validator("group_threshold")
    @classmethod
    def _validate_group_threshold(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"group_threshold must be >= 1, got {v}")
        return v

    @field_validator("bash_max_len")
    @classmethod
    def _validate_bash_max_len(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"bash_max_len must be >= 1, got {v}")
        return v

    @field_validator("throttle_ms")
    @classmethod
    def _validate_throttle_ms(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"throttle_ms must be >= 0, got {v}")
        return v

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ToolDisplayConfig):
            return NotImplemented
        return (
            self.names_threshold == other.names_threshold
            and self.group_threshold == other.group_threshold
            and self.bash_max_len == other.bash_max_len
            and self.throttle_ms == other.throttle_ms
            and self._show == other._show
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.names_threshold,
                self.group_threshold,
                self.bash_max_len,
                self.throttle_ms,
                tuple(sorted(self._show.items())),
            )
        )
