import re
from dataclasses import dataclass

# NATS subject special chars (`.`, `*`, `>`) flow from these fields into
# `lyra.typing.{platform}.{bot_id}` (and other subjects); reject anything
# outside the allowlist so a caller can never publish to a wildcard match.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True, slots=True)
class WorkScope:
    platform: str
    bot_id: str
    scope_id: int
    trace_id: str

    def __post_init__(self) -> None:
        for field, value in (("platform", self.platform), ("bot_id", self.bot_id)):
            if not _TOKEN_RE.fullmatch(value):
                raise ValueError(
                    f"WorkScope.{field} must match {_TOKEN_RE.pattern}; got {value!r}"
                )
