"""factory secrets — disaster recovery for lost NATS nkeys."""

from __future__ import annotations

import subprocess

import typer

from factory.secrets_reset import run_secrets_reset

secrets_app = typer.Typer(
    name="secrets",
    help="Manage factory secrets and disaster recovery.",
)


@secrets_app.command("reset")
def secrets_reset(
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Skip confirmation prompt (required when stdin is not a TTY).",
    ),
    ack_external_distribution: bool = typer.Option(
        False,
        "--ack-external-distribution",
        help=(
            "Acknowledge manual fan-out of external seeds (M₂ llmCLI, voiceCLI, …) "
            "after regen. Without this flag, genkeys exits 2 when externals exist."
        ),
    ),
    converge: bool = typer.Option(
        False,
        "--converge",
        help="Run make converge after refreshing Podman secrets (full stack restart).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print planned steps without executing.",
    ),
) -> None:
    """Wipe lost NATS nkeys, regenerate from acl-matrix, refresh Podman secrets.

    Use when ~/.roxabi/factory/nkeys/*.seed are lost or compromised.
    Creates a timestamped backup (nkeys.bak.{epoch}/) before wiping.

    Does NOT rotate bot tokens, OAuth, or LiteLLM keys — see
    docs/runbooks/secrets-disaster-recovery.md for the full matrix.
    """
    try:
        run_secrets_reset(
            yes=yes,
            ack_external_distribution=ack_external_distribution,
            converge=converge,
            dry_run=dry_run,
        )
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    except subprocess.CalledProcessError as exc:
        typer.echo(f"Command failed (exit {exc.returncode}): {exc.cmd}", err=True)
        raise typer.Exit(exc.returncode) from exc