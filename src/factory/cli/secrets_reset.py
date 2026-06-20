"""Destructive NATS nkey recovery — wipe seeds, regenerate, refresh Podman secrets."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from factory.paths import factory_data_dir

RunStep = Callable[[], None]
SubprocessRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class ResetPlan:
    """Ordered recovery steps for operator visibility."""

    steps: tuple[str, ...]


def factory_repo_root() -> Path:
    """Resolve the roxabi-factory checkout (deploy/install.sh must exist)."""
    env = os.environ.get("ROXABI_FACTORY_REPO")
    if env:
        root = Path(env).expanduser().resolve()
        if (root / "deploy" / "install.sh").is_file():
            return root
        raise FileNotFoundError(f"ROXABI_FACTORY_REPO invalid: {root}")

    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "deploy" / "install.sh").is_file():
            return parent
    raise FileNotFoundError(
        "factory repo root not found — run from checkout or set ROXABI_FACTORY_REPO"
    )


def build_reset_plan(*, converge: bool) -> ResetPlan:
    steps = (
        "factory-acl genkeys --regenerate (backup + wipe nkeys + regen auth.conf)",
        "./deploy/install.sh --force --secrets-only (refresh Podman secrets)",
    )
    if converge:
        steps = (*steps, "make converge (regen auth.conf mount + restart stack)")
    else:
        steps = (
            *steps,
            "manual: systemctl --user restart factory-nats + clients "
            "(or make converge)",
        )
    return ResetPlan(steps=steps)


def confirm_reset(*, assume_yes: bool) -> None:
    if assume_yes:
        return
    if not sys.stdin.isatty():
        raise SystemExit(
            "Refusing destructive reset without a TTY — pass --yes to confirm."
        )
    reply = input(
        "This will BACKUP then WIPE ~/.roxabi/factory/nkeys and regenerate all "
        "NATS identities. Continue? [y/N] "
    )
    if not reply.strip().lower().startswith("y"):
        raise SystemExit("Aborted.")


def run_nkeys_regenerate(
    repo_root: Path,
    *,
    yes: bool,
    ack_external_distribution: bool,
    run_regenerate: RunStep | None = None,
) -> None:
    matrix = repo_root / "deploy" / "nats" / "acl-matrix.json"
    args = argparse.Namespace(
        yes=yes,
        ack_external_distribution=ack_external_distribution,
        matrix=matrix,
    )

    def _default() -> None:
        from scripts._modes import _mode_regenerate

        _mode_regenerate(args)

    (run_regenerate or _default)()


def run_install_secrets(
    repo_root: Path,
    *,
    runner: SubprocessRunner = subprocess.run,
) -> None:
    install = repo_root / "deploy" / "install.sh"
    runner(
        [str(install), "--force", "--secrets-only"],
        cwd=repo_root,
        check=True,
        text=True,
    )


def run_converge(
    repo_root: Path,
    *,
    runner: SubprocessRunner = subprocess.run,
) -> None:
    runner(["make", "converge"], cwd=repo_root, check=True, text=True)


def run_secrets_reset(  # noqa: PLR0913 — injectable orchestration for tests
    *,
    yes: bool = False,
    ack_external_distribution: bool = False,
    converge: bool = False,
    dry_run: bool = False,
    repo_root: Path | None = None,
    run_regenerate: RunStep | None = None,
    runner: SubprocessRunner = subprocess.run,
) -> ResetPlan:
    """Execute wipe + regen + secrets refresh. Returns the plan for logging."""
    root = repo_root or factory_repo_root()
    plan = build_reset_plan(converge=converge)

    if dry_run:
        print(f"Repo: {root}")
        print(f"Nkeys dir: {factory_data_dir() / 'nkeys'}")
        for i, step in enumerate(plan.steps, start=1):
            print(f"  {i}. {step}")
        if ack_external_distribution:
            print("  (with --ack-external-distribution)")
        return plan

    confirm_reset(assume_yes=yes)

    print("==> Regenerating NATS nkeys (backup + wipe + provision)...")
    run_nkeys_regenerate(
        root,
        yes=True,
        ack_external_distribution=ack_external_distribution,
        run_regenerate=run_regenerate,
    )

    print("==> Refreshing Podman secrets from new seeds...")
    run_install_secrets(root, runner=runner)

    if converge:
        print("==> Running make converge...")
        run_converge(root, runner=runner)
    else:
        print(
            "==> Next: restart NATS + clients "
            "(see docs/runbooks/secrets-disaster-recovery.md)"
        )

    return plan