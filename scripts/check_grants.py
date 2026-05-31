#!/usr/bin/env python3
"""check_grants.py — assert code-required NATS subjects are covered by ACL grants.

Consumes:
  deploy/nats/code-subjects.json  — resource-keyed subject manifest
  deploy/nats/acl-matrix.json     — identity/group grant matrix

Exit 0: all required subjects are covered.
Exit 1: drift detected (uncovered subject / dead consumer-group / missing provisioner).
Exit 2: bad input (malformed JSON, missing required keys).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure repo root is on sys.path when executed directly as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._acl_models import LoadedMatrix  # noqa: E402
from scripts._effective import effective_grants, subject_covered  # noqa: E402
from scripts._loader import load_matrix  # noqa: E402


def _validate_consumer_fields(
    kind: str,
    res_name: str,
    resource: dict,  # type: ignore[type-arg]
) -> None:
    """B1+B2: validate consumer_subjects shape and mutual-requirement with consumer_group.

    Raises ValueError on any violation.
    """  # noqa: E501
    cons_subjects = resource.get("consumer_subjects")

    # B1: consumer_subjects shape.
    if cons_subjects is not None:
        if not isinstance(cons_subjects, dict):
            raise ValueError(
                f"code-subjects.json: {kind} '{res_name}': "
                f"'consumer_subjects' must be an object"
            )
        pub = cons_subjects.get("publish")
        sub = cons_subjects.get("subscribe")
        if pub is not None and not isinstance(pub, list):
            raise ValueError(
                f"code-subjects.json: {kind} '{res_name}': "
                f"'consumer_subjects.publish' must be a list"
            )
        if sub is not None and not isinstance(sub, list):
            raise ValueError(
                f"code-subjects.json: {kind} '{res_name}': "
                f"'consumer_subjects.subscribe' must be a list"
            )

    # B2: consumer_group and consumer_subjects are mutually required.
    cons_group = resource.get("consumer_group")
    has_group = bool(cons_group and isinstance(cons_group, str))
    has_subjects = bool(
        isinstance(cons_subjects, dict)
        and (cons_subjects.get("publish") or cons_subjects.get("subscribe"))
    )
    if cons_group is not None and not has_group:
        raise ValueError(
            f"code-subjects.json: {kind} '{res_name}': "
            f"'consumer_group' must be a non-empty string"
        )
    if has_group and not has_subjects:
        raise ValueError(
            f"code-subjects.json: {kind} '{res_name}': "
            f"'consumer_group' present but 'consumer_subjects' is absent "
            f"or contains no non-empty list"
        )
    if has_subjects and not has_group:
        raise ValueError(
            f"code-subjects.json: {kind} '{res_name}': "
            f"'consumer_subjects' present but 'consumer_group' is absent "
            f"or empty"
        )


def _validate_resource(kind: str, res_name: str, resource: object) -> None:
    """Validate a single resource entry from the code-subjects manifest.

    Raises ValueError on any shape violation.
    """
    if not isinstance(resource, dict):
        raise ValueError(
            f"code-subjects.json: {kind} '{res_name}' must be an object"
        )
    if "provisioner" not in resource or not isinstance(resource["provisioner"], str):
        raise ValueError(
            f"code-subjects.json: {kind} '{res_name}': "
            "missing or non-string 'provisioner'"
        )
    prov_subjects = resource.get("provisioner_subjects")
    if not isinstance(prov_subjects, dict) or "publish" not in prov_subjects:
        raise ValueError(
            f"code-subjects.json: {kind} '{res_name}': "
            f"'provisioner_subjects' must contain a 'publish' list"
        )
    if not isinstance(prov_subjects["publish"], list):
        raise ValueError(
            f"code-subjects.json: {kind} '{res_name}': "
            f"'provisioner_subjects.publish' must be a list"
        )
    _validate_consumer_fields(kind, res_name, resource)


def load_code_subjects(path: Path) -> dict:  # type: ignore[type-arg]
    """Read and minimally validate the code-subjects manifest.

    Required top-level structure:
      - at least one of "streams" or "kv_buckets" (both must be dicts if present)
      - each resource has "provisioner" (str) and "provisioner_subjects.publish" (list)

    Raises ValueError on shape violations; raises json.JSONDecodeError on bad JSON.
    """
    raw_text = path.read_text()
    parsed: object = json.loads(raw_text)

    # B1: top-level must be a JSON object, not a list or scalar.
    if not isinstance(parsed, dict):
        raise ValueError("code-subjects.json: top-level value must be a JSON object")
    data: dict = parsed  # type: ignore[type-arg]

    streams = data.get("streams")
    kv_buckets = data.get("kv_buckets")

    if streams is None and kv_buckets is None:
        raise ValueError(
            "code-subjects.json: must have at least one of 'streams' or 'kv_buckets'"
        )
    if streams is not None and not isinstance(streams, dict):
        raise ValueError("code-subjects.json: 'streams' must be an object")
    if kv_buckets is not None and not isinstance(kv_buckets, dict):
        raise ValueError("code-subjects.json: 'kv_buckets' must be an object")

    for kind, bucket in (("stream", streams or {}), ("kv", kv_buckets or {})):
        for res_name, resource in bucket.items():
            _validate_resource(kind, res_name, resource)

    return data


def _check_provisioner(
    kind: str,
    res_name: str,
    resource: dict,  # type: ignore[type-arg]
    grants: dict[str, tuple[list[str], list[str]]],
) -> list[str]:
    """Assert the provisioner identity's effective publish covers its subjects."""
    provisioner: str = resource["provisioner"]
    if provisioner not in grants:
        return [
            f"FAIL: provisioner '{provisioner}' for {kind} {res_name} is"
            " missing or retired in the matrix (cannot provision)"
        ]
    eff_pub, _ = grants[provisioner]
    errors: list[str] = []
    for subj in resource["provisioner_subjects"].get("publish", []):
        if not subject_covered(subj, eff_pub):
            errors.append(
                f"FAIL: identity '{provisioner}' publish[] does not cover"
                f" required subject '{subj}' ({kind} {res_name}, provisioner)"
            )
    return errors


def _check_consumer_group(
    kind: str,
    res_name: str,
    resource: dict,  # type: ignore[type-arg]
    matrix: LoadedMatrix,
    grants: dict[str, tuple[list[str], list[str]]],
) -> list[str]:
    """Assert every active member of the consumer-group covers its subjects."""
    cg = resource.get("consumer_group")
    if not cg:
        return []

    consumer_subjects = resource.get("consumer_subjects") or {}
    cg_publish: list[str] = consumer_subjects.get("publish", [])
    cg_subscribe: list[str] = consumer_subjects.get("subscribe", [])

    members = [
        name
        for name, identity in matrix["identities"].items()
        if identity["status"] == "active" and cg in identity.get("groups", [])
    ]
    if not members:
        return [
            f"FAIL: consumer-group '{cg}' has no active members"
            f" ({kind} {res_name}) — dead group, code expects consumers"
        ]

    errors: list[str] = []
    for member in members:
        m_pub, m_sub = grants[member]
        for subj in cg_publish:
            if not subject_covered(subj, m_pub):
                errors.append(
                    f"FAIL: identity '{member}' publish[] does not cover"
                    f" required subject '{subj}' ({kind} {res_name},"
                    f" consumer-group {cg})"
                )
        for subj in cg_subscribe:
            if not subject_covered(subj, m_sub):
                errors.append(
                    f"FAIL: identity '{member}' subscribe[] does not cover"
                    f" required subject '{subj}' ({kind} {res_name},"
                    f" consumer-group {cg})"
                )
    return errors


def run(matrix: LoadedMatrix, code_subjects: dict) -> list[str]:  # type: ignore[type-arg]
    """Check that every required subject is covered by ACL grants.

    Returns a (possibly empty) list of FAIL lines.
    """
    grants = effective_grants(matrix)

    resources: list[tuple[str, str, dict]] = []  # type: ignore[type-arg]
    for res_name, resource in (code_subjects.get("streams") or {}).items():
        resources.append(("stream", res_name, resource))
    for res_name, resource in (code_subjects.get("kv_buckets") or {}).items():
        resources.append(("kv", res_name, resource))

    errors: list[str] = []
    for kind, res_name, resource in resources:
        errors.extend(_check_provisioner(kind, res_name, resource, grants))
        errors.extend(_check_consumer_group(kind, res_name, resource, matrix, grants))
    return errors


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Assert every NATS subject the code requires (per code-subjects.json) "
            "is covered by the effective ACL grants in acl-matrix.json."
        )
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=Path("deploy/nats/acl-matrix.json"),
        help="Path to acl-matrix.json (default: deploy/nats/acl-matrix.json)",
    )
    parser.add_argument(
        "--code-subjects",
        type=Path,
        default=Path("deploy/nats/code-subjects.json"),
        help="Path to code-subjects.json (default: deploy/nats/code-subjects.json)",
    )
    args = parser.parse_args(argv)

    matrix = load_matrix(args.matrix)

    try:
        code_subjects = load_code_subjects(args.code_subjects)
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        print(f"error: code-subjects.json: {exc}", file=sys.stderr)
        sys.exit(2)

    errors = run(matrix, code_subjects)

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)

    total = len(code_subjects.get("streams") or {}) + len(
        code_subjects.get("kv_buckets") or {}
    )
    print(f"check-grants: OK ({total} resources)")
    sys.exit(0)


if __name__ == "__main__":
    main()
