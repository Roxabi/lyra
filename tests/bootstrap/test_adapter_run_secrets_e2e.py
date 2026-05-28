"""V3 RED-GATE: adapter credential path no longer touches `config.db`.

The destructive cutover (#1057) removed CredentialStore and migrated bot token
delivery to per-bot Podman secrets mounted at /run/secrets/. These boundary
tests assert the new contract via static source analysis:

1. The adapter bootstrap module pulls `load_bot_token` from the dedicated
   `lyra.bootstrap.credentials` module — not from `config.db`.
2. `bootstrap/credentials.py` references `/run/secrets/` (or its env override).
3. No imports of the deleted CredentialStore class remain in the adapter
   call chain.

Multi-bot end-to-end reading is covered by
`tests/test_bootstrap_credential_resolution.py::test_adapter_handles_multi_bot`
(Telegram) and `::test_discord_adapter_handles_multi_bot` (Discord, CR-6 N8).
These tests complement that with code-level guard rails — failing fast if a
future refactor reintroduces the legacy path.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
CREDENTIALS = PROJECT_ROOT / "src/lyra/bootstrap/credentials.py"
ADAPTER_BOOTSTRAP = PROJECT_ROOT / "src/lyra/bootstrap/composition_root.py"
WIRING = PROJECT_ROOT / "src/lyra/bootstrap/wiring/bootstrap_wiring.py"
CLI_SETUP = PROJECT_ROOT / "src/lyra/cli_setup.py"


def test_credentials_module_reads_from_run_secrets() -> None:
    """`bootstrap/credentials.py` resolves tokens from /run/secrets/."""
    src = CREDENTIALS.read_text()
    assert "load_bot_token" in src, (
        "credentials.py must define load_bot_token "
        "(reads from /run/secrets/bot_token-<bot_id>)"
    )
    assert "/run/secrets" in src or "LYRA_RUN_SECRETS_DIR" in src, (
        "credentials.py must reference the /run/secrets/ base path or its env override"
    )


def test_adapter_and_wiring_use_credentials_module() -> None:
    """The adapter + wiring entry points pull from `lyra.bootstrap.credentials`."""
    for f in (ADAPTER_BOOTSTRAP, WIRING):
        src = f.read_text()
        assert "from lyra.bootstrap import credentials" in src, (
            f"{f.relative_to(PROJECT_ROOT)} must import the credentials module"
        )
        assert "credentials.load_bot_token" in src, (
            f"{f.relative_to(PROJECT_ROOT)} must call credentials.load_bot_token"
        )


def test_credentials_has_prod_guard_on_env_override() -> None:
    """`bootstrap/credentials.py` must contain a production guard that ignores
    LYRA_RUN_SECRETS_DIR when running inside a container or when LYRA_ENV=prod
    (issue #1304)."""
    src = CREDENTIALS.read_text()
    assert "_is_prod_env" in src, (
        "credentials.py must define _is_prod_env (production detection helper)"
    )
    assert "LYRA_ENV=prod" in src or 'os.environ.get("LYRA_ENV") == "prod"' in src, (
        "credentials.py must check LYRA_ENV=prod in the production guard"
    )
    assert "/run/.containerenv" in src, (
        "credentials.py must check /run/.containerenv in the production guard"
    )
    assert "ignored in production" in src, (
        "credentials.py docstring must warn that the override is ignored in production"
    )


def test_no_credential_store_in_adapter_call_chain() -> None:
    """The deleted CredentialStore class must not be imported anywhere in the
    adapter credential path."""
    for f in (CREDENTIALS, ADAPTER_BOOTSTRAP, WIRING, CLI_SETUP):
        src = f.read_text()
        assert "CredentialStore" not in src, (
            f"{f.relative_to(PROJECT_ROOT)} must not reference CredentialStore "
            f"(class was deleted in #1057)"
        )
        assert "LyraKeyring" not in src, (
            f"{f.relative_to(PROJECT_ROOT)} must not reference LyraKeyring "
            f"(class was deleted in #1057)"
        )
