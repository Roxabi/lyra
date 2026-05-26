from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkScope:
    platform: str
    bot_id: str
    scope_id: int
    trace_id: str
