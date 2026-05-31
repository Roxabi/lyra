"""Shared expansion logic: matrix → effective grants per active identity.

Used by both _renderer.render_auth_conf (T2) and the CI falsification gate (T3+).
Lifting this out of render_auth_conf ensures a single expansion path — any
drift between the rendered config and the gate's expectation is structural,
not an artefact of two independent implementations.

#1527 S5 — ADR-079 CI falsification gate.
"""

from __future__ import annotations

from scripts._acl_models import LoadedMatrix


def effective_grants(matrix: LoadedMatrix) -> dict[str, tuple[list[str], list[str]]]:
    """{identity_name: (publish, subscribe)} for EVERY ACTIVE identity.

    Expansion order (mirrors render_auth_conf L59-81, byte-identical output):
      1. Identity's own publish / subscribe lists (in declaration order).
      2. Each group listed in identity.groups[], in list order:
           extend with group.publish then group.subscribe.
      3. Dedup both lists with dict.fromkeys (preserves first-occurrence order).
      4. Flow-inbox injection: for each request_reply_flow,
           append _inbox.{requester}.> to requester's subscribe
           and to responder's publish, iff not already present.

    Retired identities (status != "active") are excluded.
    Identities with no groups and no flows are included (inline grants only).
    """
    identities = matrix["identities"]
    flows = matrix.get("request_reply_flows", [])
    groups = matrix.get("groups", {})

    pub_allow: dict[str, list[str]] = {}
    sub_allow: dict[str, list[str]] = {}

    # Step 1-3: per-identity expansion + group injection + dedup.
    for name, identity in identities.items():
        if identity["status"] == "retired":
            continue
        pub: list[str] = list(identity.get("publish", []))
        sub: list[str] = list(identity.get("subscribe", []))
        for gname in identity.get("groups", []):
            g = groups[gname]
            pub.extend(g.get("publish", []))
            sub.extend(g.get("subscribe", []))
        pub_allow[name] = list(dict.fromkeys(pub))
        sub_allow[name] = list(dict.fromkeys(sub))

    # Step 4: flow-inbox injection (no second dedup — mirrors renderer guard).
    for flow in flows:
        requester = flow["requester"]
        responder = flow["responder"]
        inbox = f"_inbox.{requester}.>"
        if requester in sub_allow and inbox not in sub_allow[requester]:
            sub_allow[requester].append(inbox)
        if responder in pub_allow and inbox not in pub_allow[responder]:
            pub_allow[responder].append(inbox)

    return {name: (pub_allow[name], sub_allow[name]) for name in pub_allow}


def subject_covered(subject: str, grants: list[str]) -> bool:
    """NATS-wildcard-aware coverage check.

    Returns True if *subject* is covered by any grant in *grants*:
      - Exact match:     grant == subject
      - Bare wildcard:   grant == ">"
      - Suffix wildcard: grant ends with ".>" and
          subject starts with grant[:-1] (any sub-level, bare prefix excluded)
      - Single-token wildcard: grant ends with ".*" and
          subject starts with grant[:-1] and has exactly one extra token

    Semantics are identical to _subject_covered in check_request_reply_flows.py.
    """
    for grant in grants:
        if grant == subject or grant == ">":
            return True
        if grant.endswith(".>"):
            prefix = grant[:-1]  # "lyra.foo."
            if subject.startswith(prefix):
                return True
        if grant.endswith(".*"):
            prefix = grant[:-1]  # "lyra.foo."
            if subject.startswith(prefix) and "." not in subject[len(prefix):]:
                return True
    return False
