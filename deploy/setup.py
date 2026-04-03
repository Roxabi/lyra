#!/usr/bin/env python3
"""Lyra setup — clone and register modules, scaffold config, start supervisord."""

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

LYRA_DIR = Path(os.environ.get("LYRA_DIR", Path.home() / "projects" / "lyra"))
SUPERVISOR_DIR = LYRA_DIR / "deploy" / "supervisor"
STACK_FILE = Path(os.environ.get("STACK_FILE", LYRA_DIR / "deploy" / "stack.toml"))


def run(cmd: str, cwd: Path | None = None, check: bool = True) -> int:
    sys.stdout.flush()
    result = subprocess.run(cmd, shell=True, cwd=cwd)
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
        "git": ("git --version", None),
        "uv": (
            "uv --version",
            "https://docs.astral.sh/uv/getting-started/installation/",
        ),
        "supervisord": (
            "supervisord --version",
            "Run: uv tool install supervisor",
        ),
        "claude": (
            "claude --version",
            "Run: npm install -g @anthropic-ai/claude-code",
        ),
        "ssh": ("ssh -T git@github.com", None),  # exits 1 on success for GitHub
    }
    print("Checking prerequisites...")
    failed = []
    for name, (cmd, install_url) in checks.items():
        result = subprocess.run(cmd, shell=True, capture_output=True)
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


# ── Config scaffolding ───────────────────────────────────────────────────────


def scaffold_env(lyra_dir: Path) -> None:
    """Copy .env.example → .env if missing."""
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
    print("       → Edit ~/projects/lyra/.env and fill in your tokens")


def scaffold_config_toml(lyra_dir: Path) -> None:
    """Copy config.toml.example → config.toml if missing."""
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
    print("       → Edit ~/projects/lyra/config.toml and fill in your user IDs")


def setup_plugins(
    lyra_dir: Path | None,
    voicecli_dir: Path | None,
    include_optional: bool,
) -> None:
    """Register Claude Code marketplaces and install plugins."""

    result = subprocess.run("claude --version", shell=True, capture_output=True)
    if result.returncode != 0:
        print("  ✗  claude CLI not found — skipping plugin setup")
        return

    print()
    print("Claude Code plugins")
    print("─" * 40)
    print()

    # ── Register marketplaces ─────────────────────────────────────────────────

    marketplace_out = subprocess.run(
        "claude plugin marketplace list", shell=True, capture_output=True, text=True
    ).stdout

    # Local project marketplaces
    for label, path in [("lyra-marketplace", lyra_dir), ("voicecli-marketplace", voicecli_dir)]:
        if not path or not path.exists():
            continue
        if label in marketplace_out:
            print(f"  ✓  {label}  (already registered)")
        else:
            r = subprocess.run(
                f"claude plugin marketplace add {path}",
                shell=True, capture_output=True, text=True,
            )
            if r.returncode == 0:
                print(f"  ✓  {label} registered")
            else:
                print(f"  !  {label}: {r.stderr.strip() or r.stdout.strip()}")

    # agent-browser external marketplace
    if "agent-browser" not in marketplace_out:
        r = subprocess.run(
            "claude plugin marketplace add https://github.com/vercel-labs/agent-browser",
            shell=True, capture_output=True, text=True,
        )
        if r.returncode == 0:
            print("  ✓  agent-browser marketplace registered")
        else:
            print(f"  !  agent-browser marketplace: {r.stderr.strip() or r.stdout.strip()}")
    else:
        print("  ✓  agent-browser  (already registered)")

    print()

    # ── Mandatory plugins ─────────────────────────────────────────────────────

    print("  Mandatory:")
    mandatory = [
        ("web-intel",      "roxabi-marketplace",  "URL scraping & analysis"),
        ("agent-browser",  "agent-browser",       "headless browser (auth, interactive pages)"),
        ("lyra-send",      "lyra-marketplace",    "proactive messaging (Telegram & Discord)"),
        ("refine-agent",   "lyra-marketplace",    "agent profile management"),
    ]
    for name, marketplace, desc in mandatory:
        r = subprocess.run(
            f"claude plugin install {name}@{marketplace}",
            shell=True, capture_output=True, text=True,
        )
        ok = r.returncode == 0 or "already installed" in r.stdout
        print(f"    {'✓' if ok else '!'}  {name}@{marketplace} — {desc}")
        if not ok:
            print(f"         {r.stderr.strip() or r.stdout.strip()}")

    print()

    # ── Conditional: voice-cli (only if voiceCLI was installed) ──────────────

    if voicecli_dir and voicecli_dir.exists():
        r = subprocess.run(
            "claude plugin install voice-cli@voicecli-marketplace",
            shell=True, capture_output=True, text=True,
        )
        ok = r.returncode == 0 or "already installed" in r.stdout
        print(f"  {'✓' if ok else '!'}  voice-cli@voicecli-marketplace — VoiceCLI TTS/STT integration")
        print()

    # ── Optional plugins ──────────────────────────────────────────────────────

    print("  Optional:")
    optional_plugins = [
        ("dev-core",          "roxabi-marketplace", "full dev workflow (frame→spec→plan→implement→ship)"),
        ("visual-explainer",  "roxabi-marketplace", "HTML diagrams & data visualizations"),
        ("compress",          "roxabi-marketplace", "compact agent/skill definitions, save tokens"),
    ]
    for name, marketplace, desc in optional_plugins:
        if include_optional or ask(f"    Install {name}? ({desc})", default=True):
            r = subprocess.run(
                f"claude plugin install {name}@{marketplace}",
                shell=True, capture_output=True, text=True,
            )
            ok = r.returncode == 0 or "already installed" in r.stdout
            print(f"    {'✓' if ok else '!'}  {name}@{marketplace}")
            if not ok:
                print(f"         {r.stderr.strip() or r.stdout.strip()}")
        else:
            print(f"    skip  {name}")

    print()


def bootstrap_forge() -> None:
    """Create ~/.roxabi/forge/ structure and copy server files from roxabi-plugins."""
    agent_dir = Path.home() / ".roxabi/forge"
    forge_src = Path.home() / "projects" / "roxabi-plugins" / "forge"
    agent_dir.mkdir(parents=True, exist_ok=True)

    # Create per-project exploration directories
    for subdir in ("lyra/brand", "lyra/visuals", "lyra/diagrams", "_shared/diagrams"):
        (agent_dir / subdir).mkdir(parents=True, exist_ok=True)

    for name in ("serve.py", "gen-manifest.py", "index.html"):
        src = forge_src / name
        dst = agent_dir / name
        if not src.exists():
            continue
        if dst.exists():
            # Update if source is newer
            if src.stat().st_mtime <= dst.stat().st_mtime:
                continue
        shutil.copy2(src, dst)

    # Register forge conf symlink
    conf_src = forge_src / "conf.d" / "forge.conf"
    conf_dst = SUPERVISOR_DIR / "conf.d" / "forge.conf"
    if conf_src.exists() and not conf_dst.exists():
        conf_dst.parent.mkdir(parents=True, exist_ok=True)
        conf_dst.symlink_to(conf_src)

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


def init_agents(lyra_dir: Path) -> None:
    """Run lyra agent init to seed the DB from TOML files."""
    agent_init = lyra_dir / ".venv" / "bin" / "lyra"
    if not agent_init.exists():
        print("  ✗  lyra CLI not found in venv — skipping agent init")
        return
    result = subprocess.run(
        f"{agent_init} agent init",
        shell=True,
        cwd=lyra_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("  ✓  lyra agent init — agents seeded into DB")
    else:
        # Non-fatal — may fail if DB already has agents
        print(f"  !  lyra agent init skipped ({result.stderr.strip() or 'already initialized'})")


def create_log_dirs() -> None:
    """Create XDG-compliant log directories."""
    state = Path.home() / ".local" / "state"
    for app in ("lyra", "voicecli"):
        log_dir = state / app / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
    print("  ✓  Log directories created (~/.local/state/*/logs/)")


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    include_optional = "--all" in sys.argv

    with open(STACK_FILE, "rb") as f:
        config = tomllib.load(f)

    modules = config.get("modules", {})

    print("\nLyra setup")
    print("─" * 40)
    print()

    if not check_prereqs():
        sys.exit(1)

    print()

    # ── Phase 1: Clone + install + register ──────────────────────────────────

    lyra_dir = None
    voicecli_dir = None

    installed_optional: set[str] = set()

    for name, module in modules.items():
        optional = module.get("optional", False)
        path = Path(module["path"]).expanduser()

        if name == "lyra":
            lyra_dir = path
        elif name == "voiceCLI":
            voicecli_dir = path

        # Optional modules: ask unless --all was passed
        if optional and not include_optional:
            if path.exists():
                print(f"  ✓  {name}  (already at {path})")
            elif not ask(f"  Install {name}? (optional)", default=False):
                print(f"  skip  {name}")
                continue
        elif not optional:
            pass  # required — always install

        if path.exists():
            if name not in ("lyra",):  # lyra already printed above if optional check passed
                print(f"  ✓  {name}  (already at {path})")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            run(f"git clone {module['repo']} {path}")
            tag = module.get("tag", "").strip()
            if tag:
                print(f"       pinning to {tag}...")
                run(f"git checkout {tag}", cwd=path)

        print("       installing...")
        run(module.get("install", "uv sync"), cwd=path)

        if module.get("register", True):
            print("       registering...")
            subprocess.run("make register", shell=True, cwd=path, check=True)
        else:
            print("       skipping registration (no daemon)")

        if optional:
            installed_optional.add(name)
        print()

    # If voiceCLI was installed, re-sync lyra with the voice extra
    if "voiceCLI" in installed_optional and lyra_dir and lyra_dir.exists():
        print("       re-syncing lyra with voice support...")
        run("uv sync --extra voice", cwd=lyra_dir)
        print()

    # ── Phase 2: Post-setup scaffolding ──────────────────────────────────────

    print("Post-setup")
    print("─" * 40)
    print()

    create_log_dirs()

    if ask("  Install forge gallery? (optional)", default=False):
        bootstrap_forge()
    else:
        print("  skip  forge")

    if voicecli_dir and voicecli_dir.exists():
        symlink_voicecli(voicecli_dir)

    if lyra_dir and lyra_dir.exists():
        scaffold_env(lyra_dir)
        scaffold_config_toml(lyra_dir)
        init_agents(lyra_dir)

    print()

    # ── Phase 3: Claude Code plugins ─────────────────────────────────────────

    setup_plugins(lyra_dir, voicecli_dir, include_optional)

    # ── Phase 4: Start supervisord + enable systemd ───────────────────────────

    print("Starting supervisord...")
    run(str(SUPERVISOR_DIR / "start.sh"))

    print()
    print("Enabling systemd auto-start...")
    run("systemctl --user daemon-reload", check=False)
    run("systemctl --user enable lyra.service", check=False)
    run("loginctl enable-linger $(whoami)", check=False)
    print("  ✓  lyra.service enabled (auto-starts on boot)")

    # Enable monitoring timer (installed by make register)
    print()
    print("Enabling monitoring timer...")
    run("systemctl --user enable lyra-monitor.timer", check=False)
    print("  ✓  lyra-monitor.timer enabled")
    print("     Run 'make monitor enable' to start, after adding secrets to .env")

    print()
    print("─" * 40)
    print("Setup complete!")
    print()
    print("  make ps                              status of all services")
    print("  systemctl --user status lyra          systemd unit status")
    print("  make lyra reload                     restart lyra")
    print("  make tts reload                      restart voicecli_tts")
    print("  make stt reload                      restart voicecli_stt")
    print("  make monitor status                  health monitoring timer")
    print()

    # ── Remaining manual steps ───────────────────────────────────────────────

    manual_steps = []

    if lyra_dir:
        env_file = lyra_dir / ".env"
        config_file = lyra_dir / "config.toml"
        if env_file.exists():
            # Check if tokens are filled in
            content = env_file.read_text()
            if "TELEGRAM_TOKEN=\n" in content or "TELEGRAM_TOKEN=" not in content:
                manual_steps.append(
                    f"Fill in bot tokens:\n"
                    f"     nano {lyra_dir}/.env\n"
                    f"     → TELEGRAM_TOKEN, DISCORD_TOKEN, etc.\n"
                    f"     → Get Telegram token from @BotFather\n"
                    f"     → Get Discord token from discord.com/developers"
                )
        if config_file.exists():
            content = config_file.read_text()
            if "owner_users = []" in content:
                manual_steps.append(
                    f"Fill in your user IDs:\n"
                    f"     nano {lyra_dir}/config.toml\n"
                    f"     → Telegram ID: message @userinfobot\n"
                    f"     → Discord ID: Settings → Advanced → Developer Mode"
                )

    # Check if Claude is authenticated
    result = subprocess.run(
        "claude --version", shell=True, capture_output=True, text=True
    )
    if result.returncode != 0:
        manual_steps.append("Install and authenticate Claude CLI:\n     claude")
    else:
        # Claude is installed but might not be authenticated
        manual_steps.append(
            "Authenticate Claude CLI (if not already done):\n     claude"
        )

    manual_steps.append(
        "Add bot tokens to the credential store:\n"
        "     cd ~/projects/lyra && lyra bot add"
    )

    manual_steps.append(
        "Set up health monitoring:\n"
        "     1. Add to .env: TELEGRAM_TOKEN, TELEGRAM_ADMIN_CHAT_ID\n"
        "     2. Create health secret:\n"
        "        mkdir -p ~/.lyra/secrets\n"
        '        openssl rand -hex 32 > ~/.lyra/secrets/health_secret\n'
        "        chmod 600 ~/.lyra/secrets/health_secret\n"
        "     3. Add LYRA_HEALTH_SECRET=$(cat ~/.lyra/secrets/health_secret) to .env\n"
        "     4. make monitor enable"
    )

    if manual_steps:
        print("Remaining manual steps:")
        print()
        for i, step in enumerate(manual_steps, 1):
            print(f"  {i}. {step}")
            print()

    if include_optional is False and any(m.get("optional") for m in modules.values()):
        print(
            "  make setup ARGS=--all    include optional modules (imageCLI, roxabi-vault)"
        )
        print()


if __name__ == "__main__":
    main()
