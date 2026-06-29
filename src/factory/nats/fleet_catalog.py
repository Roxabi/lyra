"""Fleet manifest catalog from deploy/quadlet.toml + .container files."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

_INSTRUMENTED_COMPONENTS = frozenset(
    {
        "hub",
        "dashboard",
        "telegram",
        "discord",
        "clipool",
        "omp",
        "gh-helper",
        "turn-writer",
        "blobstore",
        "socialmedia-adapter",
        "ingress",
    }
)

_CONTAINER_NAME_RE = re.compile(r"^ContainerName=(.+)$", re.MULTILINE)
_IMAGE_RE = re.compile(r"^Image=(.+)$", re.MULTILINE)
_AUTOUPDATE_RE = re.compile(r"^Label=io\.containers\.autoupdate=registry", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class FleetCatalogEntry:
    container_name: str
    component_key: str
    image_ref: str
    systemd_unit: str
    instrumented: bool
    pinned: bool
    source: str = "manifest"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parse_container_file(path: Path) -> tuple[str, str, bool]:
    text = path.read_text(encoding="utf-8")
    name_m = _CONTAINER_NAME_RE.search(text)
    image_m = _IMAGE_RE.search(text)
    if name_m is None or image_m is None:
        raise ValueError(f"missing ContainerName or Image in {path}")
    container_name = name_m.group(1).strip()
    image_ref = image_m.group(1).strip()
    autoupdate = _AUTOUPDATE_RE.search(text) is not None
    return container_name, image_ref, not autoupdate


def load_fleet_catalog(
    *,
    quadlet_toml: Path | None = None,
    quadlet_dir: Path | None = None,
) -> list[FleetCatalogEntry]:
    """Load expected fleet rows from quadlet SSoT."""
    root = _repo_root()
    toml_path = quadlet_toml or root / "deploy" / "quadlet.toml"
    container_dir = quadlet_dir or root / "deploy" / "quadlet"
    raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    components: dict[str, dict[str, str]] = raw.get("component", {})
    entries: list[FleetCatalogEntry] = []
    for key, spec in sorted(components.items()):
        rel = spec.get("container")
        if not rel:
            continue
        container_path = container_dir / rel
        if not container_path.is_file():
            continue
        container_name, image_ref, pinned = _parse_container_file(container_path)
        entries.append(
            FleetCatalogEntry(
                container_name=container_name,
                component_key=key,
                image_ref=image_ref,
                systemd_unit=f"{container_name}.service",
                instrumented=key in _INSTRUMENTED_COMPONENTS,
                pinned=pinned,
            )
        )
    return entries