from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, NotRequired, TypedDict

Owner = Literal["lyra", "voicecli", "imagecli", "reserved"]
Status = Literal["active", "retired"]


class ContainerDeploy(TypedDict):
    type: Literal["container"]
    secret: str  # podman secret name


class HostDeploy(TypedDict):
    type: Literal["host"]
    path: str  # absolute or ~-expanded


class ExternalDeploy(TypedDict):
    type: Literal["external"]
    host: str  # resolvable hostname / MagicDNS short
    target_path: str  # path on the remote host


Deploy = ContainerDeploy | HostDeploy | ExternalDeploy


class GroupDefinition(TypedDict):
    description: NotRequired[str]
    publish: list[str]
    subscribe: list[str]


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
    deploy: NotRequired[Deploy]
    groups: NotRequired[list[str]]


class Flow(TypedDict):
    requester: str
    responder: str
    subject: str


class LoadedMatrix(TypedDict):
    version: str
    identities: dict[str, Identity]
    request_reply_flows: NotRequired[list[Flow]]
    groups: NotRequired[dict[str, GroupDefinition]]


@dataclass(eq=True, frozen=True)
class ParsedUser:
    nkey: str = field(compare=False)
    publish_allow: frozenset[str]
    subscribe_allow: frozenset[str]
    allow_responses: bool
    comment_name: str


@dataclass(eq=True, frozen=True)
class ParsedAuthConf:
    default_publish_deny: tuple[str, ...]
    default_subscribe_deny: tuple[str, ...]
    users: tuple[ParsedUser, ...]
