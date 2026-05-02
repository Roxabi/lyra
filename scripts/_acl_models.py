from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

Owner = Literal["lyra", "voicecli", "imagecli", "reserved"]
Status = Literal["active", "retired"]


class Identity(TypedDict):
    owner: Owner
    status: Status
    description: str
    publish: list[str]
    subscribe: list[str]
    allow_responses: bool
    created_at: str
    retired_at: NotRequired[str]
    notes: NotRequired[str]


class Flow(TypedDict):
    requester: str
    responder: str
    subject: str


class LoadedMatrix(TypedDict):
    version: str
    identities: dict[str, Identity]
    request_reply_flows: NotRequired[list[Flow]]


@dataclass(eq=True, frozen=True)
class ParsedUser:
    nkey: str
    publish_allow: frozenset[str]
    subscribe_allow: frozenset[str]
    allow_responses: bool
    comment_name: str


@dataclass(eq=True, frozen=True)
class ParsedAuthConf:
    default_publish_deny: tuple[str, ...]
    default_subscribe_deny: tuple[str, ...]
    users: tuple[ParsedUser, ...]
