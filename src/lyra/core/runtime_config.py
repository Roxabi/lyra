"""RuntimeConfig — mutable overlay for AnthropicAgent parameters (issue #135)."""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.core.agent import Agent

log = logging.getLogger(__name__)

_STYLES = {"concise", "detailed", "technical", "friendly"}
_STYLE_INSTRUCTIONS: dict[str, str] = {
    "detailed": (
        "Provide thorough, detailed explanations. Elaborate on context and reasoning."
    ),
    "technical": (
        "Use precise technical language. Prefer exact terms over approximations."
    ),
    "friendly": "Be warm and conversational. Use an approachable, casual tone.",
}
_DEFAULTS: dict[str, object] = {
    "style": "concise",
    "language": "auto",
    "temperature": 0.7,
    "model": None,
    "max_steps": None,
    "extra_instructions": "",
}
_VALID_PARAMS = {
    "style",
    "language",
    "temperature",
    "model",
    "max_steps",
    "extra_instructions",
}


@dataclass(frozen=True)
class EffectiveConfig:
    """Resolved configuration used by AnthropicAgent for a single process() call."""

    model: str
    temperature: float
    system_prompt: str
    max_turns: int


@dataclass
class RuntimeConfig:
    """Mutable overlay for AnthropicAgent parameters.

    Fields mirror _DEFAULTS. Defaults produce no-op overlay behaviour.
    """

    style: str = "concise"
    language: str = "auto"
    temperature: float = 0.7
    model: str | None = None
    max_steps: int | None = None
    extra_instructions: str = ""

    def overlay(self, base: Agent) -> EffectiveConfig:
        """Build EffectiveConfig by merging this overlay on top of base Agent config."""
        parts: list[str] = [base.system_prompt] if base.system_prompt else []

        # "concise" is the no-op default, intentionally absent from _STYLE_INSTRUCTIONS.
        # The != "concise" guard preserves this: even if "concise" is later added to the
        # dict, it won't be injected — keeping concise a true no-injection default.
        if self.style != "concise" and self.style in _STYLE_INSTRUCTIONS:
            parts.append(_STYLE_INSTRUCTIONS[self.style])

        if self.language != "auto":
            parts.append(f"Reply in {self.language}.")

        if self.extra_instructions:
            parts.append(self.extra_instructions)

        system_prompt = "\n\n".join(parts)

        return EffectiveConfig(
            model=self.model or base.model_config.model,
            temperature=self.temperature,
            system_prompt=system_prompt,
            max_turns=self.max_steps or base.model_config.max_turns,
        )

    def save(self, path: Path) -> None:
        """Write only non-default values to a flat TOML file."""
        data: dict[str, object] = {}
        for key, default in _DEFAULTS.items():
            value = getattr(self, key)
            if value != default:
                data[key] = value

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_write_flat_toml(data))

    @classmethod
    def load(cls, path: Path) -> RuntimeConfig:
        """Load RuntimeConfig from a TOML file.

        Returns cls() if file is absent or corrupt.
        """
        if not path.exists():
            return cls()
        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
        except Exception as exc:
            log.warning("Corrupt runtime config at %s — using defaults: %s", path, exc)
            return cls()

        rc = cls()
        for key, value in data.items():
            if key not in _VALID_PARAMS:
                log.warning("Unknown runtime config key %r in %s — skipping", key, path)
                continue
            try:
                # Convert TOML-parsed native types (float, int) to str so set_param
                # can validate and coerce them. str(0.7) → "0.7" → float("0.7") = 0.7
                # round-trips cleanly for all values that tomllib produces in practice.
                str_value = value if isinstance(value, str) else str(value)
                rc = set_param(rc, key, str_value)
            except ValueError as exc:
                log.warning(
                    "Invalid runtime config value for %r: %s — skipping", key, exc
                )
        return rc

    @classmethod
    def reset(
        cls,
        instance: RuntimeConfig | None = None,
        key: str | None = None,
    ) -> RuntimeConfig:
        """Reset all fields (key=None) or a single field to its default.

        Raises ValueError for unknown key.
        """
        if key is None:
            return cls()
        if key not in _VALID_PARAMS:
            raise ValueError(f"Unknown config key: {key!r}")
        if instance is None:
            instance = cls()
        return replace(instance, **{key: _DEFAULTS[key]})


def _write_flat_toml(data: dict) -> str:
    """Serialize a flat dict to TOML string. Caller must filter out None values."""
    lines = []
    for key, value in data.items():
        if isinstance(value, str):
            lines.append(f'{key} = "{value}"')
        elif isinstance(value, (int, float)):
            lines.append(f"{key} = {value}")
    return "\n".join(lines) + "\n" if lines else ""


def set_param(rc: RuntimeConfig, key: str, value: str) -> RuntimeConfig:
    """Validate and apply a single key=value update to RuntimeConfig.

    Returns a new RuntimeConfig instance via dataclasses.replace().
    Raises ValueError on unknown key or invalid value.
    """
    if key not in _VALID_PARAMS:
        raise ValueError(f"Unknown config key: {key!r}. Valid: {sorted(_VALID_PARAMS)}")

    parsed: object

    if key == "style":
        if value not in _STYLES:
            raise ValueError(f"Invalid style {value!r}. Valid: {sorted(_STYLES)}")
        parsed = value

    elif key == "temperature":
        try:
            fval = float(value)
        except (ValueError, TypeError):
            raise ValueError(
                f"temperature must be a float between 0 and 1, got {value!r}"
            )
        if not 0.0 <= fval <= 1.0:
            raise ValueError(f"temperature must be between 0 and 1, got {fval}")
        parsed = fval

    elif key == "max_steps":
        try:
            iv = int(value)
        except (ValueError, TypeError):
            raise ValueError(f"max_steps must be a positive integer, got {value!r}")
        if iv <= 0:
            raise ValueError(f"max_steps must be a positive integer (≥1), got {iv}")
        if iv > 50:
            raise ValueError(f"max_steps too large ({iv}). Maximum is 50.")
        parsed = iv

    elif key == "model":
        if value.lower() in ("", "none"):
            parsed = None
        else:
            import re as _re

            if not _re.match(r"^[a-zA-Z0-9_.:-]+$", value):
                raise ValueError(
                    f"Invalid model ID {value!r}. "
                    "Only alphanumerics, '.', '_', ':', '-' are allowed."
                )
            parsed = value

    elif key == "language":
        import re as _re

        if value != "auto" and not _re.match(r"^[a-z]{2,8}$", value):
            raise ValueError(
                f"Invalid language {value!r}. "
                "Use 'auto' or a 2-8 char lowercase code (e.g. 'fr', 'en')."
            )
        parsed = value

    else:
        # extra_instructions — accept as-is, but cap length to avoid bloating context
        if len(value) > 500:
            raise ValueError(
                f"extra_instructions too long ({len(value)} chars). Max is 500."
            )
        parsed = value

    return replace(rc, **{key: parsed})


class RuntimeConfigHolder:
    """Mutable single-cell container shared by AnthropicAgent and CommandRouter.

    Both hold the *same* holder instance. Mutations replace `holder.value`
    (a new RuntimeConfig), so all readers see the updated config immediately.

    Concurrency contract: designed for use on a single asyncio event loop.
    `.value` assignment is GIL-atomic in CPython — no locking is needed provided
    no coroutine yields between reading and re-assigning the same holder.
    If porting to free-threaded Python (PEP 703) or adding run_in_executor
    paths, protect mutations with an asyncio.Lock.
    """

    __slots__ = ("value",)

    def __init__(self, value: RuntimeConfig) -> None:
        self.value = value
