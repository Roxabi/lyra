#!/usr/bin/env python3
"""check_grants.py — assert code-required NATS subjects are covered by ACL grants.

Consumes:
  deploy/nats/acl-matrix.json     — identity/group grant matrix

Exit 0: all required subjects are covered.
Exit 1: drift detected (uncovered subject / dead consumer-group / missing provisioner).
Exit 2: bad input (malformed JSON, missing required keys).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure repo root is on sys.path when executed directly as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._acl_models import LoadedMatrix  # noqa: E402
from scripts._effective import effective_grants, subject_covered  # noqa: E402
from scripts._loader import load_matrix  # noqa: E402

# Embedded resource definitions (stable, small).
# Tuple: (kind, name, provisioner, provisioner_subjects, consumer_group,
#         consumer_subjects)
RESOURCES = [
    (
        "stream",
        "LYRA_OUTBOUND_AUDIO",
        "hub",
        [
            "$JS.API.STREAM.CREATE.LYRA_OUTBOUND_AUDIO",
            "$JS.API.STREAM.INFO.LYRA_OUTBOUND_AUDIO",
            "$JS.API.STREAM.UPDATE.LYRA_OUTBOUND_AUDIO",
        ],
        "audio-consumer",
        {
            "publish": [
                "$JS.API.STREAM.INFO.LYRA_OUTBOUND_AUDIO",
                "$JS.API.CONSUMER.CREATE.LYRA_OUTBOUND_AUDIO.>",
                "$JS.API.CONSUMER.INFO.LYRA_OUTBOUND_AUDIO.*",
                "$JS.API.CONSUMER.MSG.NEXT.LYRA_OUTBOUND_AUDIO.*",
                "$JS.ACK.LYRA_OUTBOUND_AUDIO.>",
            ],
            "subscribe": [],
        },
    ),
    (
        "stream",
        "LYRA_TURNS",
        "turn-writer",
        [
            "$JS.API.STREAM.CREATE.LYRA_TURNS",
            "$JS.API.STREAM.INFO.LYRA_TURNS",
            "$JS.API.STREAM.UPDATE.LYRA_TURNS",
            "$JS.API.CONSUMER.CREATE.LYRA_TURNS.turn-writer-v1.>",
            "$JS.API.CONSUMER.INFO.LYRA_TURNS.turn-writer-v1",
            "$JS.API.CONSUMER.MSG.NEXT.LYRA_TURNS.turn-writer-v1",
        ],
        None,
        None,
    ),
    (
        "kv",
        "lyra_outbound_audio_sent",
        "hub",
        [
            "$JS.API.STREAM.CREATE.KV_lyra_outbound_audio_sent",
            "$JS.API.STREAM.INFO.KV_lyra_outbound_audio_sent",
        ],
        "audio-consumer",
        {
            "publish": [
                "$JS.API.STREAM.INFO.KV_lyra_outbound_audio_sent",
                "$JS.API.STREAM.MSG.GET.KV_lyra_outbound_audio_sent",
                "$KV.lyra_outbound_audio_sent.>",
            ],
            "subscribe": [
                "$KV.lyra_outbound_audio_sent.>",
            ],
        },
    ),
]


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


def run(matrix: LoadedMatrix) -> list[str]:
    """Check that every required subject is covered by ACL grants.

    Returns a (possibly empty) list of FAIL lines.
    """
    grants = effective_grants(matrix)

    errors: list[str] = []
    for kind, res_name, provisioner, prov_subjects, cg, cg_subjects in RESOURCES:
        resource = {
            "provisioner": provisioner,
            "provisioner_subjects": {"publish": prov_subjects},
            "consumer_group": cg,
            "consumer_subjects": cg_subjects,
        }
        errors.extend(_check_provisioner(kind, res_name, resource, grants))
        errors.extend(_check_consumer_group(kind, res_name, resource, matrix, grants))
    return errors


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Assert every NATS subject the code requires"
            " is covered by the effective ACL grants in acl-matrix.json."
        )
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=Path("deploy/nats/acl-matrix.json"),
        help="Path to acl-matrix.json (default: deploy/nats/acl-matrix.json)",
    )
    args = parser.parse_args(argv)

    matrix = load_matrix(args.matrix)

    errors = run(matrix)

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)

    print(f"check-grants: OK ({len(RESOURCES)} resources)")
    sys.exit(0)


if __name__ == "__main__":
    main()
