from typing import Literal

from pydantic import BaseModel

from lyra.transport.work_scope import WorkScope


class TypingEvent(BaseModel):
    kind: Literal["started", "ended"]
    scope: WorkScope
    ts: float
