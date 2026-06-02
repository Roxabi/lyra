"""Configuration model for tool-call display in streaming responses.

Loaded by bootstrap from the ``[tool_display]`` section of config.toml.
When the section is absent, ``ToolDisplayConfig()`` is used (all defaults).
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

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
        to count-only mode (e.g. "5 edits").  Default: 5.
    bash_group_threshold:
        Number of bash commands before switching from per-command display to a
        grouped summary (e.g. "4 commands").  Default: 3.
    files_group_threshold:
        Number of distinct files before switching from per-file display to a
        grouped summary (e.g. "4 files edited").  Default: 3.
    bash_max_len:
        Maximum characters to display per bash command before truncating.
        Default: 80.
    throttle_ms:
        Minimum milliseconds between consecutive intermediate tool-card
        emissions during a single turn.  Terminal events bypass this throttle.
        Default: 2000.  Use 0 to disable throttling entirely.
    show:
        Read-only mapping of canonical tool key → visibility. A key explicitly
        set to ``False`` suppresses the corresponding tool from the recap card
        (read/grep/glob preserve silent counters). Keys NOT present in the map
        fall through to default routing — known buckets render normally; truly
        unknown tools (e.g. ``TodoWrite``, ``LS``) are tracked in
        ``unknown_calls``. To suppress an unknown tool, add it with ``false``.
        Canonical keys are lowercase snake_case (``web_fetch``, not ``webfetch``).
        Mutation raises ``TypeError``.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    names_threshold: int = 5
    bash_group_threshold: int = 3
    files_group_threshold: int = 3
    bash_max_len: int = 80
    throttle_ms: int = 2000
    """Min ms between streaming edits. Future consumers must wire through
    factory.outbound.throttle.ThrottleCapability (not per-adapter logic) per
    ADR-073 — single stage primitive, not per-platform variants."""
    # Stored as dict[str, bool] for Pydantic compatibility; exposed as
    # MappingProxyType via the .show property to preserve read-only semantics.
    _show: dict[str, bool] = {}

    @model_validator(mode="before")
    @classmethod
    def _migrate_group_threshold(cls, data: Any) -> Any:
        """Deprecated alias: group_threshold sets both bash and files thresholds."""
        if isinstance(data, dict) and data.get("group_threshold") is not None:
            data = dict(data)
            gt = data.pop("group_threshold")
            data.setdefault("bash_group_threshold", gt)
            data.setdefault("files_group_threshold", gt)
        return data

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

    @field_validator("bash_group_threshold")
    @classmethod
    def _validate_bash_group_threshold(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"bash_group_threshold must be >= 1, got {v}")
        return v

    @field_validator("files_group_threshold")
    @classmethod
    def _validate_files_group_threshold(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"files_group_threshold must be >= 1, got {v}")
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
            and self.bash_group_threshold == other.bash_group_threshold
            and self.files_group_threshold == other.files_group_threshold
            and self.bash_max_len == other.bash_max_len
            and self.throttle_ms == other.throttle_ms
            and self._show == other._show
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.names_threshold,
                self.bash_group_threshold,
                self.files_group_threshold,
                self.bash_max_len,
                self.throttle_ms,
                tuple(sorted(self._show.items())),
            )
        )
