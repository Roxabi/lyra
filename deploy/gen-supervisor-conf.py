#!/usr/bin/env python3
"""
Generator: agents.yml → supervisord conf.d/*.conf

Usage:
    uv run deploy/gen-supervisor-conf.py [--dry-run] [--output DIR]

Reads deploy/agents.yml and generates supervisor program configs.
Default output: deploy/supervisor/conf.d/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml


def validate_env_key(key: str) -> bool:
    """Validate environment variable key — only alphanumeric, underscore, hyphen."""
    import re

    return bool(re.match(r"^[A-Za-z0-9_-]+$", key))


def validate_env_value(value: str) -> bool:
    """Validate environment variable value — no newlines or quotes."""
    # Reject newlines and quotes that could break INI format
    return not any(c in value for c in "\n\r\"'")


def validate_command_override(value: str) -> bool:
    """Validate command_override — printable ASCII, no shell metacharacters.

    Accepts: alphanumerics, `/` (paths), `.`, `-`, `_`, spaces (argv split).
    Rejects: newlines, quotes, `;`, `&`, `|`, backticks, `$`, `(`, `)`, `<`, `>`.
    This is the same philosophy as validate_env_value, tightened for the
    fact that the value lands verbatim on a supervisord ``command=`` line
    which is fed to /bin/sh on program start.
    """
    import re

    # Must be non-empty and entirely within a safe character class
    return bool(value) and bool(re.match(r"^[A-Za-z0-9/_.\- ]+$", value))

# Template defaults matching existing conf.d/*.conf structure
DEFAULTS: dict[str, Any] = {
    "autostart": False,
    "autorestart": True,
    "startsecs": 5,
    "startretries": 3,
    "stopwaitsecs": 75,
    "stopasgroup": True,
    "killasgroup": True,
    "stdout_logfile_maxbytes": "10MB",
    "stdout_logfile_backups": 3,
    "stderr_logfile_maxbytes": "5MB",
    "stderr_logfile_backups": 3,
}

# Run script mapping: hub → run_hub.sh, others → run_adapter.sh <name>
RUN_HUB = "{home}/projects/lyra/deploy/supervisor/scripts/run_hub.sh"
RUN_ADAPTER = "{home}/projects/lyra/deploy/supervisor/scripts/run_adapter.sh"


def resolve_path(template: str, ctx: dict[str, str], program: str) -> str:
    """Resolve path template with variable substitution."""
    return template.format(**ctx, program=program)


def build_environment(
    ctx: dict[str, str], nkey: str | None, extra_env: dict[str, str] | None
) -> str:
    """Build the environment= line for supervisor config."""
    home = ctx["home"]
    project_dir = ctx["project_dir"]
    venv_bin = f"{project_dir}/.venv/bin"
    parts = [
        f'HOME="{home}"',
        f'PATH="{home}/.local/bin:{venv_bin}:%(ENV_PATH)s"',
    ]

    if nkey:
        # Validate nkey filename (no path traversal)
        if "/" in nkey or "\\" in nkey or not validate_env_key(nkey.replace(".", "")):
            raise ValueError(f"Invalid nkey filename: {nkey}")
        nkey_path = f"{home}/.lyra/nkeys/{nkey}"
        parts.append(f'NATS_NKEY_SEED_PATH="{nkey_path}"')

    if extra_env:
        for k, v in sorted(extra_env.items()):
            # Validate key and value to prevent config injection
            if not validate_env_key(k):
                raise ValueError(f"Invalid env key (injection risk): {k}")
            if not validate_env_value(str(v)):
                raise ValueError(f"Invalid env value for {k} (injection risk)")
            parts.append(f'{k}="{v}"')

    return ",".join(parts)


def generate_conf(
    name: str, agent: dict[str, Any], defaults: dict[str, Any], ctx: dict[str, str]
) -> str:
    """Generate a supervisor [program:...] config block."""
    program = f"lyra_{name}"

    # Merge defaults with agent overrides
    cfg = {**defaults, **agent}

    # Determine command:
    #   1. explicit command_override in agents.yml — used verbatim (for external-satellite
    #      programs like imagecli nats-serve that are not lyra CLI subcommands).
    #   2. name == "hub" — deploy/supervisor/scripts/run_hub.sh.
    #   3. default — deploy/supervisor/scripts/run_adapter.sh <name>.
    if "command_override" in agent:
        cmd_path = agent["command_override"]
        if not validate_command_override(cmd_path):
            raise ValueError(
                f"Invalid command_override for {name!r} (shell-metachar or empty): {cmd_path!r}"
            )
    elif name == "hub":
        cmd_path = RUN_HUB.format(home=ctx["home"])
    else:
        cmd_path = f"{RUN_ADAPTER.format(home=ctx['home'])} {name}"

    lines = [f"[program:{program}]"]
    lines.append(f"command={cmd_path}")

    # Helper to format value for supervisor config
    def fmt(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)

    # Directory (template resolution)
    directory = cfg.get("directory", "{project_dir}")
    lines.append(f"directory={resolve_path(directory, ctx, program)}")

    # Environment
    nkey = cfg.get("nkey")
    extra_env = cfg.get("env")
    lines.append(f"environment={build_environment(ctx, nkey, extra_env)}")

    # Priority (optional)
    if "priority" in cfg:
        lines.append(f"priority={cfg['priority']}")

    # Standard fields
    for field in [
        "autostart",
        "autorestart",
        "startsecs",
        "startretries",
        "stopwaitsecs",
        "stopasgroup",
        "killasgroup",
    ]:
        lines.append(f"{field}={fmt(cfg[field])}")

    # Log files (template resolution)
    stdout_tpl = cfg.get("stdout_logfile", "{logs_dir}/{program}.log")
    stderr_tpl = cfg.get("stderr_logfile", "{logs_dir}/{program}_error.log")
    lines.append(f"stdout_logfile={resolve_path(stdout_tpl, ctx, program)}")
    lines.append(f"stdout_logfile_maxbytes={cfg['stdout_logfile_maxbytes']}")
    lines.append(f"stdout_logfile_backups={fmt(cfg['stdout_logfile_backups'])}")
    lines.append(f"stderr_logfile={resolve_path(stderr_tpl, ctx, program)}")
    lines.append(f"stderr_logfile_maxbytes={cfg['stderr_logfile_maxbytes']}")
    lines.append(f"stderr_logfile_backups={fmt(cfg['stderr_logfile_backups'])}")

    lines.append("")  # trailing newline
    return "\n".join(lines)


def cleanup_orphaned_configs(
    output_dir: Path, expected_names: set[str], dry_run: bool
) -> list[Path]:
    """
    Remove orphaned config files not matching current agent set.

    Returns list of removed files (empty if dry_run).
    """
    if not output_dir.exists():
        return []

    expected_files = {f"lyra_{name}.conf" for name in expected_names}
    removed: list[Path] = []

    for conf_file in output_dir.glob("lyra_*.conf"):
        if conf_file.name not in expected_files:
            if dry_run:
                print(f"Would remove orphaned: {conf_file}")
            else:
                conf_file.unlink()
                print(f"Removed orphaned: {conf_file}")
                removed.append(conf_file)

    return removed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate supervisord conf.d from agents.yml"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print configs without writing"
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Output directory (default: deploy/supervisor/conf.d)",
    )
    parser.add_argument(
        "--agents-file",
        type=Path,
        default=None,
        help="Path to agents.yml (default: deploy/agents.yml)",
    )
    args = parser.parse_args()

    # Locate agents.yml
    script_dir = Path(__file__).parent
    agents_file = args.agents_file or script_dir / "agents.yml"

    if not agents_file.exists():
        print(f"Error: agents.yml not found at {agents_file}", file=sys.stderr)
        return 1

    # Load agents.yml
    with open(agents_file) as f:
        data = yaml.safe_load(f)

    # Determine output directory
    output_dir = args.output or script_dir / "supervisor" / "conf.d"
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    # Context for path templates
    home = "%(ENV_HOME)s"  # Supervisor variable
    ctx = {
        "home": home,
        "project_dir": f"{home}/projects/lyra",
        "logs_dir": f"{home}/.local/state/lyra/logs",
    }

    # Merge file-level defaults with code defaults
    file_defaults = data.get("defaults", {})
    merged_defaults = {**DEFAULTS, **file_defaults}

    agents = data.get("agents", {})
    if not agents:
        print("Error: no agents defined in agents.yml", file=sys.stderr)
        return 1

    # Generate each agent
    for name, agent_cfg in agents.items():
        conf_content = generate_conf(name, agent_cfg, merged_defaults, ctx)

        if args.dry_run:
            print(f"--- {name} ---")
            print(conf_content)
        else:
            output_path = output_dir / f"lyra_{name}.conf"
            with open(output_path, "w") as f:
                f.write(conf_content)
            print(f"Generated: {output_path}")

    # Cleanup orphaned configs (files for agents no longer in agents.yml)
    if not args.dry_run:
        cleanup_orphaned_configs(output_dir, set(agents.keys()), dry_run=False)

    return 0


if __name__ == "__main__":
    sys.exit(main())
