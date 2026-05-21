"""V3 RED-GATE: adapter credential path no longer touches `config.db`.

The destructive cutover (#1057) removed CredentialStore and migrated bot token
delivery to per-bot Podman secrets mounted at /run/secrets/. These boundary
tests assert the new contract via static source analysis:

1. The adapter bootstrap module does not mention `config.db` in any
   credential-loading context — it only opens `config.db` for stores that
   genuinely belong there (TurnStore, AgentStore for non-credential reads).
2. The adapter bootstrap module reads bot tokens from `/run/secrets/` via
   the `_load_bot_token` helper.
3. No imports of the deleted CredentialStore class remain in the adapter
   call chain.

Multi-bot end-to-end reading is covered by
`tests/test_bootstrap_credential_resolution.py::test_adapter_handles_multi_bot`
(T8), which exercises the real bootstrap with two bots and asserts that each
adapter receives its own token. These tests complement that with code-level
guard rails — failing fast if a future refactor reintroduces the legacy path.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
ADAPTER_BOOTSTRAP = PROJECT_ROOT / "src/lyra/bootstrap/standalone/adapter_standalone.py"
WIRING = PROJECT_ROOT / "src/lyra/bootstrap/wiring/bootstrap_wiring.py"
CLI_SETUP = PROJECT_ROOT / "src/lyra/cli_setup.py"


def test_adapter_bootstrap_reads_tokens_from_run_secrets() -> None:
    """The adapter bootstrap path resolves tokens via `_load_bot_token`."""
    src = ADAPTER_BOOTSTRAP.read_text()
    assert "_load_bot_token" in src, (
        "adapter_standalone.py must define or reference _load_bot_token "
        "(reads from /run/secrets/bot_token-<bot_id>)"
    )
    assert "/run/secrets" in src or "LYRA_RUN_SECRETS_DIR" in src, (
        "adapter_standalone.py must reference the /run/secrets/ "
        "base path or its env override"
    )


def test_adapter_bootstrap_does_not_import_credential_store() -> None:
    """The deleted CredentialStore class must not be imported anywhere in the
    adapter credential path."""
    for f in (ADAPTER_BOOTSTRAP, WIRING, CLI_SETUP):
        src = f.read_text()
        assert "CredentialStore" not in src, (
            f"{f.relative_to(PROJECT_ROOT)} must not reference CredentialStore "
            f"(class was deleted in #1057)"
        )
        assert "LyraKeyring" not in src, (
            f"{f.relative_to(PROJECT_ROOT)} must not reference LyraKeyring "
            f"(class was deleted in #1057)"
        )


def test_adapter_bootstrap_does_not_read_config_db_for_credentials() -> None:
    """No `config.db` reference within ~3 lines of a credential keyword.

    `config.db` may still appear for legitimate non-credential reads
    (AgentStore, PrefsStore opened to read per-bot settings then closed).
    This test asserts no `config.db` line is co-located with a credential
    keyword like `token`, `secret`, or `cred`.
    """
    src = ADAPTER_BOOTSTRAP.read_text().splitlines()
    cred_re = re.compile(r"\b(token|secret|cred)\b", re.IGNORECASE)
    for i, line in enumerate(src):
        if "config.db" in line:
            window = src[max(0, i - 3) : i + 4]
            window_text = "\n".join(window).lower()
            # Skip the legitimate "AgentStore/PrefsStore opens config.db" reads
            # — those are NOT credential reads.
            if any(s in window_text for s in ("agentstore", "prefsstore")):
                continue
            assert not cred_re.search("\n".join(window)), (
                f"adapter_standalone.py L{i + 1}: 'config.db' co-located with "
                f"a credential keyword — credentials must come from "
                f"/run/secrets/, not config.db"
            )
