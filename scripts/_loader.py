from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from scripts._acl_models import Flow, GroupDefinition, Identity, LoadedMatrix

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALID_VERSIONS = {"1", "2", "3", "4"}
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


def _validate_deploy(name: str, data: dict[str, object]) -> None:
    dtype = data.get("type")
    if dtype == "container":
        if "secret" not in data:
            _die(f"identity '{name}': deploy.container missing 'secret'")
    elif dtype == "host":
        if "path" not in data:
            _die(f"identity '{name}': deploy.host missing 'path'")
    elif dtype == "external":
        if "host" not in data or "target_path" not in data:
            _die(f"identity '{name}': deploy.external missing 'host' or 'target_path'")
    else:
        _die(f"identity '{name}': deploy.type invalid: {dtype!r}")


def _validate_identity(name: str, data: dict, version: str) -> Identity:
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

    if "deploy" in data:
        _validate_deploy(name, data["deploy"])  # type: ignore[arg-type]
    elif version in {"3", "4"} and data.get("status") == "active":
        _die(f"identity '{name}': v{version} requires 'deploy' for active identities")

    return data  # type: ignore[return-value]


def _parse_groups(raw_groups: dict) -> dict[str, GroupDefinition]:
    groups: dict[str, GroupDefinition] = {}
    for gname, gdata in raw_groups.items():
        if not isinstance(gdata, dict):
            _die(f"group '{gname}': must be an object")
        if "publish" not in gdata or not isinstance(gdata["publish"], list):
            _die(f"group '{gname}': missing or invalid 'publish' list")
        if not all(isinstance(s, str) for s in gdata["publish"]):
            _die(f"group '{gname}': 'publish' entries must be strings")
        if "subscribe" not in gdata or not isinstance(gdata["subscribe"], list):
            _die(f"group '{gname}': missing or invalid 'subscribe' list")
        if not all(isinstance(s, str) for s in gdata["subscribe"]):
            _die(f"group '{gname}': 'subscribe' entries must be strings")
        groups[gname] = gdata  # type: ignore[assignment]
    return groups


def _validate_group_refs(
    identities: dict[str, Identity], groups: dict[str, GroupDefinition]
) -> None:
    for name, identity in identities.items():
        for ref in identity.get("groups", []):  # type: ignore[union-attr]
            if ref not in groups:
                _die(f"identity '{name}': references unknown group '{ref}'")


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
        identities[name] = _validate_identity(name, data, version)

    flows: list[Flow] = []
    if version in {"2", "3", "4"}:
        raw_flows = raw.get("request_reply_flows", [])
        seen: set[tuple[str, str]] = set()
        for flow in raw_flows:
            pair = (flow["requester"], flow["responder"])
            if pair in seen:
                r, s = pair
                _die(f"duplicate flow pair (requester='{r}', responder='{s}')")
            seen.add(pair)
            flows.append(flow)

    groups = _parse_groups(raw.get("groups", {}))
    _validate_group_refs(identities, groups)

    return LoadedMatrix(
        version=version,
        identities=identities,
        request_reply_flows=flows,
        groups=groups,
    )
