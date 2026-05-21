#!/usr/bin/env python3
"""Lyra setup — clone optional modules, scaffold config, seed agents, install Quadlet units.

Run from a fresh checkout:

    cd ~/projects/lyra
    python3 deploy/setup.py            # interactive
    python3 deploy/setup.py --all      # install all optional modules without prompts

Prereqs (checked on entry): git, uv, podman, claude, GitHub SSH access.
"""

import os
import shutil
import socket
import subprocess
import sys
import tomllib
from pathlib import Path

LYRA_DIR = Path(os.environ.get("LYRA_DIR", Path.home() / "projects" / "lyra"))
HOSTS_TOML = Path(os.environ.get("HOSTS_TOML", Path.home() / "projects" / "hosts.toml"))


def get_host_roles(hostname: str | None = None) -> set[str]:
    """Read ~/projects/hosts.toml, return roles for current host.

    Returns empty set if hosts.toml is missing or hostname is not listed
    (non-destructive fallback — caller should warn and treat as no-roles).
    """
    name = hostname or socket.gethostname()
    if not HOSTS_TOML.exists():
        return set()
    with open(HOSTS_TOML, "rb") as f:
        data = tomllib.load(f)
    entry = data.get("host", {}).get(name)
    if not entry:
        return set()
    return set(entry.get("roles", []))


# Hardcoded optional module registry — replaces the legacy deploy/stack.toml.
# Lyra (this repo) is always installed by the caller before setup.py runs.
OPTIONAL_MODULES: list[dict[str, object]] = [
    {
        "name": "voiceCLI",
        "repo": "git@github.com:Roxabi/voiceCLI.git",
        "path": Path.home() / "projects" / "voiceCLI",
        "install": "uv sync",
        "description": "TTS/STT (requires NVIDIA GPU, ~3 GB)",
        "requires_role": "voice-worker",
    },
    {
        "name": "imageCLI",
        "repo": "git@github.com:Roxabi/imageCLI.git",
        "path": Path.home() / "projects" / "imageCLI",
        "install": "uv sync",
        "description": "Image generation CLI",
        "requires_role": "image-worker",
    },
    {
        "name": "roxabi-vault",
        "repo": "git@github.com:Roxabi/roxabi-vault.git",
        "path": Path.home() / "projects" / "roxabi-vault",
        "install": "uv sync",
        "description": "Knowledge vault",
        "requires_role": None,
    },
]


def run(cmd: list[str] | str, cwd: Path | None = None, check: bool = True) -> int:
    sys.stdout.flush()
    use_shell = isinstance(cmd, str)
    result = subprocess.run(cmd, shell=use_shell, cwd=cwd)
    if check and result.returncode != 0:
        print(f"  ✗  Command failed: {cmd}")
        sys.exit(result.returncode)
    return result.returncode


def ask(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    try:
        resp = input(f"{prompt} [{hint}] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    if not resp:
        return default
    return resp in ("y", "yes")


def check_prereqs() -> bool:
    checks = {
        "git": (["git", "--version"], None),
        "uv": (
            ["uv", "--version"],
            "https://docs.astral.sh/uv/getting-started/installation/",
        ),
        "podman": (
            ["podman", "--version"],
            "apt install podman (ships natively on Ubuntu 26.04+)",
        ),
        "claude": (
            ["claude", "--version"],
            "npm install -g @anthropic-ai/claude-code",
        ),
        "ssh": (["ssh", "-T", "git@github.com"], None),  # exits 1 on success for GitHub
    }
    print("Checking prerequisites...")
    failed = []
    for name, (cmd, install_url) in checks.items():
        result = subprocess.run(cmd, capture_output=True)
        ok = result.returncode in (0, 1) if name == "ssh" else result.returncode == 0
        print(
            f"  {'✓' if ok else '✗'}  {name}"
            + (f"  →  {install_url}" if not ok and install_url else "")
        )
        if not ok:
            failed.append(name)
    if failed:
        print("\nFix the above before running setup.")
        print("Tip: run deploy/provision.sh to install all prerequisites.")
        return False
    return True


# ── Module installation ─────────────────────────────────────────────────────


def install_lyra(lyra_dir: Path) -> None:
    print("Installing lyra...")
    run(["uv", "sync"], cwd=lyra_dir)
    print("  ✓  lyra installed")


def install_optional_module(
    module: dict, include_all: bool, host_roles: set[str]
) -> Path | None:
    name = module["name"]
    path = Path(module["path"]).expanduser()
    desc = module["description"]
    requires_role = module.get("requires_role")

    # Role filter: skip silently when host has known roles but lacks the required one.
    # When host_roles is empty (fallback), skip the filter to preserve old behaviour.
    if host_roles and requires_role and requires_role not in host_roles:
        print(f"  skip  {name}  (host lacks role '{requires_role}')")
        return None

    if path.exists():
        print(f"  ✓  {name}  (already at {path})")
        return path

    if not include_all and not ask(f"  Install {name}? ({desc})", default=False):
        print(f"  skip  {name}")
        return None

    path.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", module["repo"], str(path)])
    print("       installing...")
    install_cmd = module["install"]
    run(install_cmd if isinstance(install_cmd, list) else install_cmd.split(), cwd=path)
    return path


# ── Config scaffolding ──────────────────────────────────────────────────────


def scaffold_env(lyra_dir: Path) -> None:
    env_file = lyra_dir / ".env"
    example = lyra_dir / ".env.example"
    if env_file.exists():
        print("  ✓  .env already exists")
        return
    if not example.exists():
        print("  ✗  .env.example not found — skipping")
        return
    shutil.copy(example, env_file)
    print("  ✓  .env created from .env.example")
    print(f"       → Edit {lyra_dir}/.env and fill in DEPLOY_HOST/DEPLOY_DIR")


def scaffold_config_toml(lyra_dir: Path) -> None:
    config_file = lyra_dir / "config.toml"
    example = lyra_dir / "config.toml.example"
    if config_file.exists():
        print("  ✓  config.toml already exists")
        return
    if not example.exists():
        print("  ✗  config.toml.example not found — skipping")
        return
    shutil.copy(example, config_file)
    print("  ✓  config.toml created from config.toml.example")
    print(f"       → Edit {lyra_dir}/config.toml and fill in your user IDs")


def init_agents(lyra_dir: Path) -> None:
    """Run lyra agent init to seed the DB from TOML files."""
    agent_init = lyra_dir / ".venv" / "bin" / "lyra"
    if not agent_init.exists():
        print("  ✗  lyra CLI not found in venv — skipping agent init")
        return
    result = subprocess.run(
        [str(agent_init), "agent", "init"],
        cwd=lyra_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("  ✓  lyra agent init — agents seeded into DB")
    else:
        # Non-fatal — may fail if DB already has agents
        print(
            "  !  lyra agent init skipped "
            f"({result.stderr.strip() or 'already initialized'})"
        )


def create_log_dirs() -> None:
    """Create XDG-compliant log directories used by Quadlet bind mounts."""
    state = Path.home() / ".local" / "state"
    for app in ("lyra", "voicecli"):
        log_dir = state / app / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
    print("  ✓  Log directories created (~/.local/state/*/logs/)")


def bootstrap_forge() -> None:
    """Create ~/.roxabi/forge/ structure and copy server files from roxabi-plugins."""
    agent_dir = Path.home() / ".roxabi/forge"
    forge_src = Path.home() / "projects" / "roxabi-plugins" / "forge"
    agent_dir.mkdir(parents=True, exist_ok=True)

    for subdir in ("lyra/brand", "lyra/visuals", "lyra/diagrams", "_shared/diagrams"):
        (agent_dir / subdir).mkdir(parents=True, exist_ok=True)

    for name in ("serve.py", "gen-manifest.py", "index.html"):
        src = forge_src / name
        dst = agent_dir / name
        if not src.exists():
            continue
        if dst.exists() and src.stat().st_mtime <= dst.stat().st_mtime:
            continue
        shutil.copy2(src, dst)

    print("  ✓  Forge gallery bootstrapped (~/.roxabi/forge/)")


def symlink_voicecli(voicecli_dir: Path) -> None:
    """Symlink voicecli venv binary to ~/.local/bin/."""
    venv_bin = voicecli_dir / ".venv" / "bin" / "voicecli"
    local_bin = Path.home() / ".local" / "bin" / "voicecli"
    if local_bin.exists() or local_bin.is_symlink():
        print("  ✓  voicecli already on PATH")
        return
    if not venv_bin.exists():
        print("  ✗  voicecli venv binary not found — skipping symlink")
        return
    local_bin.parent.mkdir(parents=True, exist_ok=True)
    local_bin.symlink_to(venv_bin)
    print(f"  ✓  voicecli symlinked → {local_bin}")


# ── Claude Code plugins ─────────────────────────────────────────────────────


def setup_plugins(
    lyra_dir: Path | None,
    voicecli_dir: Path | None,
    include_optional: bool,
) -> None:
    """Register Claude Code marketplaces and install plugins."""
    result = subprocess.run(["claude", "--version"], capture_output=True)
    if result.returncode != 0:
        print("  ✗  claude CLI not found — skipping plugin setup")
        return

    print()
    print("Claude Code plugins")
    print("─" * 40)
    print()

    marketplace_out = subprocess.run(
        ["claude", "plugin", "marketplace", "list"],
        capture_output=True,
        text=True,
    ).stdout

    for label, path in [
        ("lyra-marketplace", lyra_dir),
        ("voicecli-marketplace", voicecli_dir),
    ]:
        if not path or not path.exists():
            continue
        if label in marketplace_out:
            print(f"  ✓  {label}  (already registered)")
        else:
            r = subprocess.run(
                ["claude", "plugin", "marketplace", "add", str(path)],
                capture_output=True,
                text=True,
            )
            if r.returncode == 0:
                print(f"  ✓  {label} registered")
            else:
                print(f"  !  {label}: {r.stderr.strip() or r.stdout.strip()}")

    if "agent-browser" not in marketplace_out:
        r = subprocess.run(
            [
                "claude",
                "plugin",
                "marketplace",
                "add",
                "https://github.com/vercel-labs/agent-browser",
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            print("  ✓  agent-browser marketplace registered")
        else:
            print(
                "  !  agent-browser marketplace: "
                f"{r.stderr.strip() or r.stdout.strip()}"
            )
    else:
        print("  ✓  agent-browser  (already registered)")

    print()

    print("  Mandatory:")
    mandatory = [
        ("web-intel", "roxabi-marketplace", "URL scraping & analysis"),
        (
            "agent-browser",
            "agent-browser",
            "headless browser (auth, interactive pages)",
        ),
        ("lyra-send", "lyra-marketplace", "proactive messaging (Telegram & Discord)"),
        ("refine-agent", "lyra-marketplace", "agent profile management"),
    ]
    for name, marketplace, desc in mandatory:
        r = subprocess.run(
            ["claude", "plugin", "install", f"{name}@{marketplace}"],
            capture_output=True,
            text=True,
        )
        ok = r.returncode == 0 or "already installed" in r.stdout
        print(f"    {'✓' if ok else '!'}  {name}@{marketplace} — {desc}")
        if not ok:
            print(f"         {r.stderr.strip() or r.stdout.strip()}")

    print()

    if voicecli_dir and voicecli_dir.exists():
        r = subprocess.run(
            ["claude", "plugin", "install", "voice-cli@voicecli-marketplace"],
            capture_output=True,
            text=True,
        )
        ok = r.returncode == 0 or "already installed" in r.stdout
        print(
            f"  {'✓' if ok else '!'}  voice-cli@voicecli-marketplace — "
            "VoiceCLI TTS/STT integration"
        )
        print()

    print("  Optional:")
    optional_plugins = [
        (
            "dev-core",
            "roxabi-marketplace",
            "full dev workflow (frame→spec→plan→implement→ship)",
        ),
        (
            "visual-explainer",
            "roxabi-marketplace",
            "HTML diagrams & data visualizations",
        ),
        (
            "compress",
            "roxabi-marketplace",
            "compact agent/skill definitions, save tokens",
        ),
    ]
    for name, marketplace, desc in optional_plugins:
        if include_optional or ask(f"    Install {name}? ({desc})", default=True):
            r = subprocess.run(
                ["claude", "plugin", "install", f"{name}@{marketplace}"],
                capture_output=True,
                text=True,
            )
            ok = r.returncode == 0 or "already installed" in r.stdout
            print(f"    {'✓' if ok else '!'}  {name}@{marketplace}")
            if not ok:
                print(f"         {r.stderr.strip() or r.stdout.strip()}")
        else:
            print(f"    skip  {name}")

    print()


# ── Quadlet install + auto-start ────────────────────────────────────────────


def install_quadlet_units(lyra_dir: Path, host_roles: set[str]) -> None:
    # If we know the roles AND lyra-hub is not in them, skip cleanly.
    if host_roles and "lyra-hub" not in host_roles:
        print("  skip  Quadlet install (host lacks 'lyra-hub' role)")
        return
    print("Installing Quadlet units (lyra)...")
    result = subprocess.run(["make", "quadlet-install"], cwd=lyra_dir)
    if result.returncode == 0:
        print("  ✓  Quadlet units installed at ~/.config/containers/systemd/")
    else:
        print("  ✗  make quadlet-install failed — aborting before linger.")
        print("     Fix the prereqs (Podman, perms) and re-run setup.py.")
        sys.exit(1)


def enable_linger(host_roles: set[str]) -> None:
    container_roles = {"lyra-hub", "voice-worker", "llm-worker", "image-worker"}
    if host_roles and not (host_roles & container_roles):
        print("  skip  linger (host runs no containers)")
        return
    print("Enabling systemd linger...")
    user = os.environ.get("USER") or os.environ.get("LOGNAME")
    if not user:
        print("  !  Could not determine current user; skipping linger.")
        return
    result = subprocess.run(["loginctl", "enable-linger", user], check=False)
    if result.returncode == 0:
        print("  ✓  Linger enabled (containers auto-start on boot)")
    else:
        print("  !  loginctl enable-linger failed — run manually:")
        print(f"       sudo loginctl enable-linger {user}")


# ── Main ────────────────────────────────────────────────────────────────────


def _print_host_roles(hostname: str, host_roles: set[str]) -> None:
    """Print host/role banner; warn when hostname unknown or hosts.toml missing."""
    if host_roles:
        print(f"Host: {hostname} → roles: {sorted(host_roles)}")
    elif not HOSTS_TOML.exists():
        print(f"  !  {HOSTS_TOML} not found — proceeding without role filter")
    else:
        print(
            f"  !  Host '{hostname}' not in {HOSTS_TOML}"
            " — proceeding without role filter"
        )


def main() -> None:
    include_optional = "--all" in sys.argv

    print("\nLyra setup")
    print("─" * 40)
    print()

    hostname = socket.gethostname()
    host_roles = get_host_roles(hostname)
    _print_host_roles(hostname, host_roles)
    print()

    if not check_prereqs():
        sys.exit(1)
    print()

    lyra_dir = LYRA_DIR
    voicecli_dir: Path | None = None

    # Phase 1: install lyra (this repo)
    install_lyra(lyra_dir)
    print()

    # Phase 2: optional sibling modules
    print("Optional modules")
    print("─" * 40)
    for module in OPTIONAL_MODULES:
        installed_path = install_optional_module(module, include_optional, host_roles)
        if module["name"] == "voiceCLI" and installed_path:
            voicecli_dir = installed_path
    print()

    # Re-sync lyra with voice extra if voiceCLI was installed
    if voicecli_dir:
        print("Re-syncing lyra with voice support...")
        run(["uv", "sync", "--extra", "voice"], cwd=lyra_dir)
        print()

    # Phase 3: post-setup scaffolding
    print("Post-setup scaffolding")
    print("─" * 40)
    print()
    create_log_dirs()
    if ask("  Install forge gallery? (optional)", default=False):
        bootstrap_forge()
    else:
        print("  skip  forge")
    if voicecli_dir:
        symlink_voicecli(voicecli_dir)
    scaffold_env(lyra_dir)
    scaffold_config_toml(lyra_dir)
    init_agents(lyra_dir)
    print()

    # Phase 4: Claude Code plugins
    setup_plugins(lyra_dir, voicecli_dir, include_optional)

    # Phase 5: Quadlet install + linger
    install_quadlet_units(lyra_dir, host_roles)
    enable_linger(host_roles)

    print()
    print("─" * 40)
    print("Setup complete!")
    print()
    print("  systemctl --user status 'lyra-*.service'  unit status")
    print("  make lyra reload                          restart all containers")
    print("  make lyra logs                            tail journalctl")
    print()

    # Manual steps
    manual_steps: list[str] = []
    config_file = lyra_dir / "config.toml"
    if config_file.exists() and "owner_users = []" in config_file.read_text():
        manual_steps.append(
            f"Fill in your user IDs in config.toml:\n     nano {lyra_dir}/config.toml"
        )

    manual_steps.append(
        "Generate NATS nkeys + Podman secrets (production hub only):\n"
        "     make nats-setup\n"
        "     make quadlet-secrets-install"
    )

    manual_steps.append(
        "Add bot tokens to the encrypted credential store:\n"
        "     lyra bot add --platform telegram --bot-id lyra\n"
        "     lyra bot add --platform discord --bot-id lyra"
    )

    manual_steps.append(
        "Authenticate the Claude CLI:\n     claude   # follow prompts to log in"
    )

    manual_steps.append(
        "Start Quadlet containers:\n"
        "     make lyra start  # OR: systemctl --user start lyra-nats lyra-hub lyra-telegram lyra-discord lyra-clipool"
    )

    if manual_steps:
        print("Remaining manual steps:")
        print()
        for i, step in enumerate(manual_steps, 1):
            print(f"  {i}. {step}")
            print()

    print(
        "Note: Health monitoring (lyra-monitor.{service,timer}) is DEPRECATED. "
        "Replacement tracked in #1035 (Monitoring v2 — NATS + Tauri desktop dashboard)."
    )


if __name__ == "__main__":
    main()
