"""Host-side fleet image digest state — STALE_IMAGE vs registry tag (#fleet Block 6)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

ImageDigestStatus = Literal["current", "stale", "unknown_compare", "n/a"]

_DEFAULT_STATE_PATH = Path("/home/factory/.roxabi/factory/state/fleet-digests.json")


@dataclass(frozen=True, slots=True)
class FleetDigestRow:
    container_name: str
    image_ref: str
    running_digest: str | None
    registry_digest: str | None
    status: ImageDigestStatus
    checked_at: str


def compare_image_digests(
    running_digest: str | None,
    registry_digest: str | None,
) -> ImageDigestStatus:
    """Classify running vs registry digest (bare sha256 hex)."""
    if not running_digest or not registry_digest:
        return "unknown_compare"
    if running_digest == registry_digest:
        return "current"
    return "stale"


def _state_path() -> Path:
    override = os.environ.get("FLEET_DIGEST_STATE_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return _DEFAULT_STATE_PATH


def load_fleet_digest_state(path: Path | None = None) -> dict[str, FleetDigestRow]:
    """Load host-polled digest rows keyed by container_name."""
    target = path or _state_path()
    if not target.is_file():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        log.warning("fleet_digest: could not read %s (%s)", target, exc)
        return {}
    rows = raw.get("rows") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return {}
    out: dict[str, FleetDigestRow] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        name = str(item.get("container_name", "")).strip()
        if not name:
            continue
        status = item.get("status", "unknown_compare")
        if status not in ("current", "stale", "unknown_compare", "n/a"):
            status = "unknown_compare"
        out[name] = FleetDigestRow(
            container_name=name,
            image_ref=str(item.get("image_ref", "")),
            running_digest=_optional_hex(item.get("running_digest")),
            registry_digest=_optional_hex(item.get("registry_digest")),
            status=status,
            checked_at=str(item.get("checked_at", "")),
        )
    return out


def _optional_hex(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().removeprefix("sha256:")
    return cleaned or None