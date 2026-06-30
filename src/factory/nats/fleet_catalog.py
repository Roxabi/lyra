"""Fleet manifest catalog from deploy/quadlet.toml + .container files."""

from __future__ import annotations

import logging
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

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
    """Resolve checkout root (contains deploy/quadlet.toml)."""
    env = os.environ.get("ROXABI_FACTORY_REPO")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "deploy" / "quadlet.toml").is_file():
            return parent
    # src/factory/nats/fleet_catalog.py → parents[3] == repo root
    return here.parents[3]


def _resolve_container_path(
    container_dir: Path, rel: str, *, template: bool
) -> Path | None:
    """Return readable quadlet unit file (.container or .container.tmpl)."""
    direct = container_dir / rel
    if direct.is_file():
        return direct
    if template or not rel.endswith(".container"):
        tmpl = container_dir / f"{rel}.tmpl"
        if tmpl.is_file():
            return tmpl
    stem = Path(rel).stem
    tmpl = container_dir / f"{stem}.container.tmpl"
    if tmpl.is_file():
        return tmpl
    return None


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
    toml_path = quadlet_toml or Path(
        os.environ.get("FLEET_QUADLET_TOML", root / "deploy" / "quadlet.toml")
    )
    container_dir = quadlet_dir or Path(
        os.environ.get("FLEET_QUADLET_DIR", root / "deploy" / "quadlet")
    )
    if not toml_path.is_file():
        raise FileNotFoundError(f"quadlet.toml not found: {toml_path}")
    raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    components: dict[str, dict[str, object]] = raw.get("component", {})
    entries: list[FleetCatalogEntry] = []
    for key, spec in sorted(components.items()):
        rel = spec.get("container")
        if not rel or not isinstance(rel, str):
            continue
        template = bool(spec.get("template"))
        container_path = _resolve_container_path(
            container_dir, rel, template=template
        )
        if container_path is None:
            log.debug(
                "fleet_catalog: skipping %s — no unit file for %s",
                key,
                rel,
            )
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