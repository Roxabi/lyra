#!/usr/bin/env python3
"""Poll running container digests vs GHCR registry tags (host-side, Block 6)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factory.nats.fleet_catalog import (  # noqa: E402
    FleetCatalogEntry,
    load_fleet_catalog,
)
from factory.nats.fleet_digest import (  # noqa: E402
    compare_image_digests,
)


@dataclass(frozen=True, slots=True)
class _PollResult:
    container_name: str
    image_ref: str
    running_digest: str | None
    registry_digest: str | None
    status: str
    checked_at: str


def _run(cmd: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    return out or None


def _normalize_digest(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().removeprefix("sha256:") or None


def _running_digest(container_name: str) -> str | None:
    if _run(["podman", "container", "exists", container_name]) is None:
        return None
    image_id = _run(
        [
            "podman",
            "container",
            "inspect",
            container_name,
            "--format",
            "{{.Image}}",
        ]
    )
    if not image_id:
        return None
    digest = _run(["podman", "image", "inspect", image_id, "--format", "{{.Digest}}"])
    return _normalize_digest(digest)


def _registry_digest(image_ref: str) -> str | None:
    out = _run(["skopeo", "inspect", f"docker://{image_ref}"])
    if not out:
        return None
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return None
    digest = payload.get("Digest")
    if not isinstance(digest, str):
        return None
    return _normalize_digest(digest)


def poll_catalog(
    catalog: list[FleetCatalogEntry],
    *,
    include_non_instrumented: bool = False,
) -> list[_PollResult]:
    now = datetime.now(tz=UTC).isoformat()
    rows: list[_PollResult] = []
    for entry in catalog:
        if entry.pinned:
            rows.append(
                _PollResult(
                    container_name=entry.container_name,
                    image_ref=entry.image_ref,
                    running_digest=None,
                    registry_digest=None,
                    status="n/a",
                    checked_at=now,
                )
            )
            continue
        if not entry.instrumented and not include_non_instrumented:
            continue
        running = _running_digest(entry.container_name)
        registry = _registry_digest(entry.image_ref)
        status = compare_image_digests(running, registry)
        rows.append(
            _PollResult(
                container_name=entry.container_name,
                image_ref=entry.image_ref,
                running_digest=running,
                registry_digest=registry,
                status=status,
                checked_at=now,
            )
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home() / ".roxabi" / "factory" / "state" / "fleet-digests.json",
    )
    parser.add_argument(
        "--include-non-instrumented",
        action="store_true",
        help="Also poll third-party manifest rows (Loki, NATS, …)",
    )
    args = parser.parse_args()
    catalog = load_fleet_catalog()
    rows = poll_catalog(
        catalog,
        include_non_instrumented=args.include_non_instrumented,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "rows": [asdict(row) for row in rows],
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    stale = sum(1 for row in rows if row.status == "stale")
    print(
        f"fleet_digest_poll: wrote {len(rows)} row(s) to {args.output} ({stale} stale)",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())