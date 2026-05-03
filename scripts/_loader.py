from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from scripts._acl_models import Flow, Identity, LoadedMatrix

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALID_VERSIONS = {"1", "2"}
_VALID_STATUSES = {"active", "retired"}
_VALID_OWNERS = {"lyra", "voicecli", "imagecli", "reserved"}
_REQUIRED_FIELDS = (
    "owner",
    "status",
    "description",
    "publish",
    "subscribe",
    "allow_responses",
    "created_at",
)


def _die(msg: str) -> None:
    print(f"error: acl-matrix.json: {msg}", file=sys.stderr)
    sys.exit(1)


def _validate_identity(name: str, data: dict) -> Identity:
    for field in _REQUIRED_FIELDS:
        if field not in data:
            _die(f"identity '{name}': missing field '{field}'")

    status = data["status"]
    if status not in _VALID_STATUSES:
        _die(f"identity '{name}': invalid status '{status}'")

    owner = data["owner"]
    if owner not in _VALID_OWNERS:
        _die(f"identity '{name}': invalid owner '{owner}'")

    created_at = data["created_at"]
    if not _DATE_RE.match(created_at):
        _die(f"identity '{name}': invalid date format for created_at: '{created_at}'")

    if status == "retired" and "retired_at" in data:
        retired_at = data["retired_at"]
        if not _DATE_RE.match(retired_at):
            _die(
                f"identity '{name}': invalid date format for retired_at: '{retired_at}'"
            )

    return data  # type: ignore[return-value]


def load_matrix(path: Path) -> LoadedMatrix:
    if not path.exists():
        print(f"error: acl-matrix.json: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    raw = json.loads(path.read_text())

    version = raw.get("version", "")
    if version not in _VALID_VERSIONS:
        _die(f"unsupported version: {version}")

    identities: dict[str, Identity] = {}
    for name, data in raw.get("identities", {}).items():
        identities[name] = _validate_identity(name, data)

    flows: list[Flow] = []
    if version == "2":
        raw_flows = raw.get("request_reply_flows", [])
        seen: set[tuple[str, str]] = set()
        for flow in raw_flows:
            pair = (flow["requester"], flow["responder"])
            if pair in seen:
                r, s = pair
                _die(f"duplicate flow pair (requester='{r}', responder='{s}')")
            seen.add(pair)
            flows.append(flow)

    return LoadedMatrix(
        version=version, identities=identities, request_reply_flows=flows
    )
