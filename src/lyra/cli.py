"""lyra — unified CLI for Lyra by Roxabi.

Entry points:
    lyra                     → start the server
    lyra start               → explicit start
    lyra --version / -V      → print version
    lyra agent create        → interactive agent wizard
    lyra agent list          → list all agents
    lyra agent validate      → validate agent TOML
    lyra config show         → display parsed config.toml
    lyra config validate     → validate config.toml

Backward compat:
    lyra-agent               → deprecated alias for `lyra agent`
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import tomllib

import typer

from lyra.cli_agent import agent_app  # noqa: F401 — re-exported for tests
from lyra.cli_bot import bot_app
from lyra.cli_ops import ops_app
from lyra.cli_setup import setup_app
from lyra.cli_voice_smoke import voice_smoke_app

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------


def _get_version() -> str:
    try:
        return importlib.metadata.version("lyra")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0-dev"


_VERSION = _get_version()

# ---------------------------------------------------------------------------
# App tree
# ---------------------------------------------------------------------------

lyra_app = typer.Typer(
    name="lyra",
    help="Lyra by Roxabi — personal AI agent engine.",
    no_args_is_help=False,
)
config_app = typer.Typer(name="config", help="Manage instance config (config.toml).")

lyra_app.add_typer(agent_app, name="agent")
lyra_app.add_typer(config_app, name="config")
lyra_app.add_typer(bot_app, name="bot")
lyra_app.add_typer(setup_app, name="setup")
lyra_app.add_typer(voice_smoke_app, name="voice-smoke")
lyra_app.add_typer(ops_app, name="ops")

hub_app = typer.Typer(name="hub", help="Run standalone Hub process (requires NATS).")
lyra_app.add_typer(hub_app, name="hub")

adapter_app = typer.Typer(
    name="adapter",
    help="Run standalone adapter process (requires NATS).",
)
lyra_app.add_typer(adapter_app, name="adapter")

# ---------------------------------------------------------------------------
# lyra hub
# ---------------------------------------------------------------------------


@hub_app.callback(invoke_without_command=True)
def _hub_callback(ctx: typer.Context) -> None:
    """Start the standalone Hub process connected to NATS."""
    if ctx.invoked_subcommand is None:
        _run_hub()


def _boot(coro_factory) -> None:
    """Load config, set up logging, and run an async bootstrap function.

    Every ``lyra <subcommand>`` entry point delegates here so logging
    is always configured before the event loop starts.
    """
    from lyra.__main__ import _setup_logging
    from lyra.bootstrap.factory.config import _load_logging_config, _load_raw_config

    raw_config = _load_raw_config()
    _setup_logging(_load_logging_config(raw_config))
    asyncio.run(coro_factory(raw_config))


def _run_hub() -> None:
    from lyra.bootstrap.standalone.hub_standalone import _bootstrap_hub_standalone

    _boot(_bootstrap_hub_standalone)


# ---------------------------------------------------------------------------
# lyra adapter
# ---------------------------------------------------------------------------


@adapter_app.command("telegram")
def _adapter_telegram() -> None:
    """Start the standalone Telegram adapter connected to NATS."""
    _run_adapter("telegram")


@adapter_app.command("discord")
def _adapter_discord() -> None:
    """Start the standalone Discord adapter connected to NATS."""
    _run_adapter("discord")


@adapter_app.command("stt")
def _adapter_stt() -> None:
    """Start the standalone STT adapter connected to NATS."""
    from lyra.bootstrap.standalone.stt_adapter_standalone import (
        _bootstrap_stt_adapter_standalone,
    )

    _boot(_bootstrap_stt_adapter_standalone)


@adapter_app.command("tts")
def _adapter_tts() -> None:
    """Start the standalone TTS adapter connected to NATS."""
    from lyra.bootstrap.standalone.tts_adapter_standalone import (
        _bootstrap_tts_adapter_standalone,
    )

    _boot(_bootstrap_tts_adapter_standalone)


def _run_adapter(platform: str) -> None:
    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

    _boot(lambda raw: _bootstrap_adapter_standalone(raw, platform))


# ---------------------------------------------------------------------------
# lyra (root)
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"lyra {_VERSION}")
        raise typer.Exit()


@lyra_app.callback(invoke_without_command=True)
def _root_callback(
    ctx: typer.Context,
    version: bool = typer.Option(  # noqa: B008
        False,
        "--version",
        "-V",
        help="Print version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Lyra by Roxabi — personal AI agent engine.

    Run without a subcommand to start the server.
    """
    if ctx.invoked_subcommand is None:
        _run_server()


# ---------------------------------------------------------------------------
# lyra start
# ---------------------------------------------------------------------------


@lyra_app.command()
def start() -> None:
    """Start the Lyra server (Telegram + Discord adapters)."""
    _run_server()


def _run_server() -> None:
    from lyra.bootstrap.factory.unified import _bootstrap_unified

    _boot(_bootstrap_unified)


# ---------------------------------------------------------------------------
# lyra config
# ---------------------------------------------------------------------------

_CONFIG_PATH_OPT: str = typer.Option(
    "config.toml", "--config", "-c", help="Path to config.toml."
)


def _load_raw(path: str) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        typer.echo(f"Error: config file not found: {path}")
        raise typer.Exit(1)
    except tomllib.TOMLDecodeError as e:
        typer.echo(f"Error: TOML parse error — {e}")
        raise typer.Exit(1)


@config_app.command(name="show")
def config_show(
    config: str = _CONFIG_PATH_OPT,
) -> None:
    """Display the parsed config.toml."""
    raw = _load_raw(config)

    defaults = raw.get("defaults", {})
    if defaults:
        typer.echo("[defaults]")
        for k, v in defaults.items():
            typer.echo(f"  {k} = {v!r}")

    admin_ids = raw.get("admin", {}).get("user_ids", [])
    typer.echo(f"\n[admin]  {len(admin_ids)} user(s)")
    for uid in admin_ids:
        typer.echo(f"  {uid}")

    tg_bots = raw.get("telegram", {}).get("bots", [])
    typer.echo(f"\n[telegram]  {len(tg_bots)} bot(s)")
    for b in tg_bots:
        typer.echo(f"  {b.get('bot_id')}  →  agent={b.get('agent')}")

    dc_bots = raw.get("discord", {}).get("bots", [])
    typer.echo(f"\n[discord]  {len(dc_bots)} bot(s)")
    for b in dc_bots:
        typer.echo(f"  {b.get('bot_id')}  →  agent={b.get('agent')}")

    agent_overrides = raw.get("agents", {})
    if agent_overrides:
        typer.echo(f"\n[agents]  {len(agent_overrides)} override(s)")
        for aname, vals in agent_overrides.items():
            typer.echo(f"  {aname}: {vals}")


@config_app.command(name="validate")
def config_validate(
    config: str = _CONFIG_PATH_OPT,
) -> None:
    """Validate config.toml (bots, env vars, auth sections)."""
    from lyra.config import load_multibot_config

    raw = _load_raw(config)

    errors: list[str] = []

    try:
        load_multibot_config(raw)
    except ValueError as e:
        errors.append(str(e))

    if errors:
        for err in errors:
            typer.echo(f"Warning: {err}")
        raise typer.Exit(1)

    typer.echo("config.toml: OK")
    tg_bots = raw.get("telegram", {}).get("bots", [])
    dc_bots = raw.get("discord", {}).get("bots", [])
    typer.echo(f"  {len(tg_bots)} Telegram bot(s), {len(dc_bots)} Discord bot(s)")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def lyra_main() -> None:
    """Entry point for the `lyra` CLI."""
    lyra_app()


def agent_main() -> None:
    """Deprecated entry point — use `lyra agent` instead."""
    typer.echo(
        "Warning: lyra-agent is deprecated, use 'lyra agent ...' instead.", err=True
    )
    agent_app()


# Keep old name for any direct imports
main = lyra_main
