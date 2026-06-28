#!/usr/bin/env python3
"""Generate docs/workflows/flows.json from machine-readable SSoT + overlay.

Sources (Tier A — automatic):
  - deploy/nats/acl-matrix.json   — identities, request_reply_flows, pub/sub
  - deploy/quadlet.toml           — container topology
  - .importlinter                 — layer map
  - packages/*/                   — workspace packages

Overlay (Tier B — hand-maintained):
  - docs/workflows/flows.overlay.json — in-process flows, payload annotations

Usage:
  uv run python tools/generate_workflows.py [REPO_ROOT]
"""

from __future__ import annotations

import configparser
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

_PLATFORM_ADAPTERS = (
    ("telegram", "telegram-adapter"),
    ("discord", "discord-adapter"),
    ("web", "web-adapter"),
)

_JETSTREAM_DISPATCH = (
    ("factory.jobs.claude", "hub", "clipool-worker", "Clipool job dispatch"),
    ("factory.jobs.omp", "hub", "omp-worker", "OMP job dispatch"),
    ("factory.turns.write", "hub", "turn-writer", "Turn logging (JetStream)"),
)

_WORKER_RESULT_SUBJECTS = (
    ("factory.job.*.progress", "clipool-worker", "hub"),
    ("factory.job.*.result", "clipool-worker", "hub"),
    ("factory.job.*.progress", "omp-worker", "hub"),
    ("factory.job.*.result", "omp-worker", "hub"),
    ("factory.gh.mint_failure.>", "gh-helper", "hub"),
)


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_toml(path: Path) -> dict:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]
    with open(path, "rb") as f:
        return tomllib.load(f)


def _parse_importlinter_layers(root: Path) -> list[str]:
    path = root / ".importlinter"
    if not path.exists():
        return []
    config = configparser.ConfigParser()
    config.read(path)
    for section in config.sections():
        if section.startswith("importlinter:contract:"):
            if config.get(section, "type", fallback="") == "layers":
                raw = config.get(section, "layers", fallback="")
                layers: list[str] = []
                for line in raw.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    for part in line.split("|"):
                        layers.append(part.strip())
                return layers
    return []


def _proc_id(identity: str) -> str:
    return f"proc-{identity}"


def _package_id(name: str) -> str:
    return f"pkg-{name}"


def _layer_id(layer: str) -> str:
    return f"layer-{layer.replace('.', '-')}"


def build_groups() -> list[dict]:
    return [
        {"id": "process", "label": "NATS Processes", "order": 1, "color": "#3b82f6"},
        {"id": "packages", "label": "Workspace Packages", "order": 2, "color": "#6366f1"},
        {"id": "layers", "label": "Code Layers", "order": 3, "color": "#8b5cf6"},
        {"id": "ci", "label": "CI / Deploy", "order": 4, "color": "#ef4444"},
        {"id": "inprocess", "label": "In-Process (overlay)", "order": 5, "color": "#ec4899"},
        {"id": "stores", "label": "Stores / Data", "order": 6, "color": "#64748b"},
    ]


def build_process_components(acl: dict, topology: dict) -> list[dict]:
    components: list[dict] = []
    identities = acl.get("identities", {})
    quadlet = topology.get("component", {})

    quadlet_by_identity: dict[str, str] = {
        "hub": "hub",
        "telegram-adapter": "telegram",
        "discord-adapter": "discord",
        "web-adapter": "dashboard",
        "clipool-worker": "clipool",
        "turn-writer": "turn-writer",
        "blobstore": "blobstore",
        "omp-worker": "omp",
        "socialmedia-adapter": "socialmedia-adapter",
        "gh-helper": "gh-helper",
        "voice-stt": "voice-stt",
        "voice-tts": "voice-tts",
        "llm-worker": "llm-worker",
        "image-worker": "image-worker",
    }

    for name, ident in sorted(identities.items()):
        if ident.get("status") == "retired":
            continue
        qkey = quadlet_by_identity.get(name)
        qcomp = quadlet.get(qkey, {}) if qkey else {}
        container = qcomp.get("container", ident.get("deploy", {}).get("type", ""))
        desc = ident.get("description", "")
        components.append(
            {
                "id": _proc_id(name),
                "label": name,
                "group": "process",
                "path": container or f"deploy/nats identity:{name}",
                "description": desc,
            }
        )
    return components


def build_package_components(root: Path) -> list[dict]:
    components: list[dict] = []
    packages_dir = root / "packages"
    if not packages_dir.is_dir():
        return components
    for pkg_dir in sorted(packages_dir.iterdir()):
        if not pkg_dir.is_dir() or pkg_dir.name == "shared":
            continue
        if not (pkg_dir / "pyproject.toml").exists():
            continue
        components.append(
            {
                "id": _package_id(pkg_dir.name),
                "label": pkg_dir.name,
                "group": "packages",
                "path": f"packages/{pkg_dir.name}",
                "description": "Workspace package",
            }
        )
    return components


def build_layer_components(layers: list[str]) -> list[dict]:
    return [
        {
            "id": _layer_id(layer),
            "label": layer.removeprefix("factory."),
            "group": "layers",
            "path": f"src/{layer.replace('.', '/')}",
            "description": "importlinter layer",
        }
        for layer in layers
        if layer.startswith("factory.")
    ]


def build_ci_components() -> list[dict]:
    return [
        {
            "id": "ci-github-actions",
            "label": "GitHub Actions",
            "group": "ci",
            "path": ".github/workflows/publish.yml",
            "description": "CI publish pipeline",
        },
        {
            "id": "ci-docker-bake",
            "label": "docker-bake.hcl",
            "group": "ci",
            "path": "docker-bake.hcl",
            "description": "Multi-target image bake",
        },
        {
            "id": "ci-ghcr",
            "label": "GHCR",
            "group": "ci",
            "path": "ghcr.io/roxabi/factory",
            "description": "Container registry",
        },
        {
            "id": "ci-quadlet",
            "label": "Quadlet deploy",
            "group": "ci",
            "path": "deploy/quadlet",
            "description": "Podman/systemd units on M₁",
        },
    ]


def _step(
    from_id: str,
    to_id: str,
    label: str,
    payload: str,
) -> dict:
    return {"from": from_id, "to": to_id, "label": label, "payload": payload}


def build_nats_flows(acl: dict) -> list[dict]:
    flows: list[dict] = []

    for entry in acl.get("request_reply_flows", []):
        req = entry["requester"]
        resp = entry["responder"]
        subject = entry["subject"]
        flows.append(
            {
                "id": f"nats-rr-{req}-to-{resp}-{subject.replace('.', '-').replace('>', 'w')}",
                "label": f"{req} → {resp} ({subject})",
                "category": "NATS request-reply",
                "summary": f"Declared in acl-matrix.json request_reply_flows",
                "source": "generated",
                "steps": [
                    _step(
                        _proc_id(req),
                        _proc_id(resp),
                        "request-reply",
                        f"Subject: {subject} (acl-matrix request_reply_flows)",
                    )
                ],
            }
        )

    for platform, adapter in _PLATFORM_ADAPTERS:
        flows.append(
            {
                "id": f"nats-inbound-{platform}",
                "label": f"Inbound message ({platform})",
                "category": "NATS messaging",
                "summary": f"Adapter publishes user messages; hub consumes factory.inbound.{platform}.>",
                "source": "generated",
                "steps": [
                    _step(
                        _proc_id(adapter),
                        _proc_id("hub"),
                        "publish inbound",
                        f"factory.inbound.{platform}.> — Core NATS, Messages plane",
                    )
                ],
            }
        )
        flows.append(
            {
                "id": f"nats-outbound-{platform}",
                "label": f"Outbound response ({platform})",
                "category": "NATS messaging",
                "summary": f"Hub publishes response chunks; adapter delivers to platform API",
                "source": "generated",
                "steps": [
                    _step(
                        _proc_id("hub"),
                        _proc_id(adapter),
                        "publish outbound",
                        f"factory.outbound.{platform}.> — Core NATS, Messages plane",
                    )
                ],
            }
        )

    for subject, publisher, subscriber, title in _JETSTREAM_DISPATCH:
        flows.append(
            {
                "id": f"nats-js-{publisher}-to-{subscriber}-{subject.split('.')[-1]}",
                "label": title,
                "category": "NATS JetStream",
                "summary": f"{publisher} publishes {subject}; {subscriber} consumes",
                "source": "generated",
                "steps": [
                    _step(
                        _proc_id(publisher),
                        _proc_id(subscriber),
                        "JetStream dispatch",
                        f"Subject: {subject}",
                    )
                ],
            }
        )

    for subject, publisher, subscriber in _WORKER_RESULT_SUBJECTS:
        flows.append(
            {
                "id": f"nats-result-{publisher}-to-{subscriber}-{subject.replace('.', '-').replace('*', 'x')}",
                "label": f"{publisher} → {subscriber} ({subject})",
                "category": "NATS worker results",
                "summary": f"Worker publishes {subject}; hub subscribes",
                "source": "generated",
                "steps": [
                    _step(
                        _proc_id(publisher),
                        _proc_id(subscriber),
                        "publish result",
                        f"Subject: {subject}",
                    )
                ],
            }
        )

    return flows


def build_ci_flows() -> list[dict]:
    return [
        {
            "id": "ci-container-publish",
            "label": "Container publish (GHCR)",
            "category": "CI / Deploy",
            "summary": "push staging or factory/v* tag → bake → GHCR → Quadlet pull on M₁",
            "source": "generated",
            "steps": [
                _step(
                    "ci-github-actions",
                    "ci-docker-bake",
                    "Trigger publish.yml",
                    "on: push branches [staging] OR tags factory/v*",
                ),
                _step(
                    "ci-docker-bake",
                    "ci-ghcr",
                    "Bake + push",
                    "Targets: agent-runtime, svc-runtime → ghcr.io/roxabi/factory",
                ),
                _step(
                    "ci-ghcr",
                    "ci-quadlet",
                    "Deploy pull",
                    "M₁ Podman pulls :staging-svc or semver pin",
                ),
            ],
        }
    ]


def merge_overlay(base: dict, overlay: dict) -> dict:
    groups = {g["id"]: g for g in base.get("groups", [])}
    for g in overlay.get("groups", []):
        groups[g["id"]] = g

    components = {c["id"]: c for c in base.get("components", [])}
    for c in overlay.get("components", []):
        components[c["id"]] = c

    flows = base.get("flows", []) + overlay.get("flows", [])

    return {
        "meta": base["meta"],
        "groups": sorted(groups.values(), key=lambda g: g.get("order", 99)),
        "components": sorted(components.values(), key=lambda c: (c.get("group", ""), c["id"])),
        "flows": flows,
    }


def generate(root: Path) -> dict:
    acl_path = root / "deploy" / "nats" / "acl-matrix.json"
    quadlet_path = root / "deploy" / "quadlet.toml"
    overlay_path = root / "docs" / "workflows" / "flows.overlay.json"

    acl = _load_json(acl_path)
    topology = _load_toml(quadlet_path)
    layers = _parse_importlinter_layers(root)

    overlay: dict = {"groups": [], "components": [], "flows": []}
    if overlay_path.exists():
        overlay = _load_json(overlay_path)

    components: list[dict] = []
    components.extend(build_process_components(acl, topology))
    components.extend(build_package_components(root))
    components.extend(build_layer_components(layers))
    components.extend(build_ci_components())

    flows: list[dict] = []
    flows.extend(build_nats_flows(acl))
    flows.extend(build_ci_flows())

    generated_count = len(flows)
    overlay_count = len(overlay.get("flows", []))

    base = {
        "meta": {
            "title": "factory — Package & Component Workflows",
            "version": "1.0.0",
            "updated": date.today().isoformat(),
            "description": "Generated from acl-matrix.json + quadlet.toml + importlinter + overlay.",
            "generated": {
                "flows": generated_count,
                "components": len(components),
                "sources": [
                    "deploy/nats/acl-matrix.json",
                    "deploy/quadlet.toml",
                    ".importlinter",
                    "packages/*",
                ],
            },
            "overlay": {
                "path": "docs/workflows/flows.overlay.json",
                "flows": overlay_count,
            },
        },
        "groups": build_groups(),
        "components": components,
        "flows": flows,
    }

    return merge_overlay(base, overlay)


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    output = root / "docs" / "workflows" / "flows.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    data = generate(root)
    output.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    gen = data["meta"]["generated"]["flows"]
    ovl = data["meta"]["overlay"]["flows"]
    total = len(data["flows"])
    print(
        f"Generated {output} — {total} flows "
        f"({gen} auto + {ovl} overlay), {len(data['components'])} components"
    )


if __name__ == "__main__":
    main()