from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkScope:
    platform: str
    bot_id: str
    scope_id: int
    trace_id: str

    def __post_init__(self) -> None:
        if not self.trace_id.strip():
            raise ValueError("WorkScope.trace_id must be non-empty")
