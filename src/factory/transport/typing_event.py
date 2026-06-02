from typing import Literal

from pydantic import BaseModel

from factory.transport.work_scope import WorkScope


class TypingEvent(BaseModel):
    kind: Literal["started", "ended"]
    scope: WorkScope
    ts: float
