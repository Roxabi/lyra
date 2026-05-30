"""Tests for tools/code_inventory.py — semantic oracle.

Contract:
  - Module in src/ resolves as kind=module, exists=True.
  - Module not in src/ (project prefix) resolves as kind=module, exists=False.
  - Symbol defined in src/ resolves as kind=symbol, exists=True.
  - Qualified module.Symbol resolves correctly.
  - Substring collision: WorkerPool does NOT resolve via WorkerPoolClient.
  - NATS subject (exact) → kind=subject, exists=True.
  - NATS subject (wildcard pattern) → kind=subject, exists=True.
  - NATS subject not in set → kind=subject, exists=False.
  - Subject NOT mistaken for module (lyra.clipool.cmd is subject, not dead module).
  - src/ path exists → kind=path, exists=True.
  - src/ path does not exist → kind=path, exists=False.
  - Directory traversal rejected → kind=path, exists=False.
  - Builtin name → kind=symbol, exists=True.
  - Imported name (re-exported) → kind=symbol, exists=True.
  - External/third-party → kind=unknown.
  - Template/glob tokens → kind=unknown (no false positive).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tools.code_inventory import CodeInventory, Verdict, _is_template_token

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _make_src_module(root: Path, rel: str, content: str = "") -> Path:
    """Create a Python source file under root/src/."""
    p = root / "src" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content or "# empty\n", encoding="utf-8")
    return p


def _make_pkg_module(root: Path, pkg: str, rel: str, content: str = "") -> Path:
    """Create a Python source file under root/packages/<pkg>/src/."""
    p = root / "packages" / pkg / "src" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content or "# empty\n", encoding="utf-8")
    return p


def _acl_matrix(root: Path, subjects: list[str]) -> None:
    """Write a minimal acl-matrix.json with the given subjects in one identity."""
    acl_dir = root / "deploy" / "nats"
    acl_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "version": "3",
        "request_reply_flows": [],
        "identities": {
            "test-identity": {
                "status": "active",
                "allow_responses": False,
                "publish": subjects,
                "subscribe": [],
            }
        },
    }
    (acl_dir / "acl-matrix.json").write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# T1 — live module in src/ → exists=True, kind=module
# ---------------------------------------------------------------------------


def test_module_live_src(tmp_path: Path) -> None:
    """A .py file under src/ is resolved as an existing module."""
    _make_src_module(tmp_path, "lyra/core/hub.py", "# hub\n")
    inv = CodeInventory.build(tmp_path)
    v = inv.resolve("lyra.core.hub")
    assert v == Verdict(exists=True, kind="module")


# ---------------------------------------------------------------------------
# T2 — module NOT in src/ but project prefix → exists=False, kind=module
# ---------------------------------------------------------------------------


def test_module_dead_project_prefix(tmp_path: Path) -> None:
    """A dotted lyra.* token with no backing file resolves as dead (exists=False).

    When a valid module prefix exists (lyra.core) but the suffix is lowercase
    and not in symbols, the oracle cannot distinguish a dead submodule from a
    dead subject reference — it returns kind=subject as a conservative guess.
    Either way exists=False is correct; the gate catches the violation.
    """
    _make_src_module(tmp_path, "lyra/__init__.py")
    inv = CodeInventory.build(tmp_path)
    v = inv.resolve("lyra.core.ghostmodule")
    # Token is in project namespace → exists=False (dead reference)
    assert v.exists is False
    # kind is subject or module — both result in the ref being flagged
    assert v.kind in ("module", "subject")


# ---------------------------------------------------------------------------
# T3 — symbol defined in src/ → exists=True, kind=symbol
# ---------------------------------------------------------------------------


def test_symbol_live(tmp_path: Path) -> None:
    """A class defined at top level in src/ resolves as an existing symbol."""
    _make_src_module(tmp_path, "lyra/core/hub.py", "class Hub: ...\n")
    inv = CodeInventory.build(tmp_path)
    v = inv.resolve("Hub")
    assert v == Verdict(exists=True, kind="symbol")


def test_symbol_dead(tmp_path: Path) -> None:
    """A PascalCase name not in src/ resolves as dead symbol."""
    _make_src_module(tmp_path, "lyra/__init__.py")
    inv = CodeInventory.build(tmp_path)
    v = inv.resolve("ZzzGhostClass")
    assert v.exists is False
    assert v.kind == "symbol"


# ---------------------------------------------------------------------------
# T4 — qualified module.ClassName resolves correctly
# ---------------------------------------------------------------------------


def test_qualified_symbol_live(tmp_path: Path) -> None:
    """lyra.core.hub.Hub resolves as live symbol (module + class in that module)."""
    _make_src_module(tmp_path, "lyra/core/hub.py", "class Hub: ...\n")
    inv = CodeInventory.build(tmp_path)
    v = inv.resolve("lyra.core.hub.Hub")
    assert v == Verdict(exists=True, kind="symbol")


def test_qualified_symbol_dead(tmp_path: Path) -> None:
    """lyra.core.hub.GhostClass → dead symbol (module exists, class absent)."""
    _make_src_module(tmp_path, "lyra/core/hub.py", "class Hub: ...\n")
    inv = CodeInventory.build(tmp_path)
    v = inv.resolve("lyra.core.hub.GhostClass")
    assert v.exists is False
    assert v.kind == "symbol"


def test_qualified_four_segment_symbol_live(tmp_path: Path) -> None:
    """lyra.outbound.emitter.OutboundEmitter resolves live across four segments."""
    _make_src_module(
        tmp_path, "lyra/outbound/emitter.py", "class OutboundEmitter: ...\n"
    )
    inv = CodeInventory.build(tmp_path)
    v = inv.resolve("lyra.outbound.emitter.OutboundEmitter")
    assert v == Verdict(exists=True, kind="symbol")


# ---------------------------------------------------------------------------
# T5 — substring collision: WorkerPool must NOT resolve via WorkerPoolClient
# ---------------------------------------------------------------------------


def test_substring_collision_no_false_resolve(tmp_path: Path) -> None:
    """WorkerPool does not match just because WorkerPoolClient exists."""
    _make_src_module(
        tmp_path, "lyra/transport/pool.py", "class WorkerPoolClient: ...\n"
    )
    inv = CodeInventory.build(tmp_path)
    # WorkerPoolClient must exist
    assert inv.resolve("WorkerPoolClient") == Verdict(exists=True, kind="symbol")
    # WorkerPool must be dead (no substring contamination)
    v = inv.resolve("WorkerPool")
    assert v.exists is False
    assert v.kind == "symbol"


# ---------------------------------------------------------------------------
# T6 — NATS subject (exact) → exists=True, kind=subject
# ---------------------------------------------------------------------------


def test_subject_exact_live(tmp_path: Path) -> None:
    """An exact subject from acl-matrix.json resolves as existing subject."""
    _acl_matrix(tmp_path, ["lyra.clipool.cmd"])
    inv = CodeInventory.build(tmp_path)
    v = inv.resolve("lyra.clipool.cmd")
    assert v == Verdict(exists=True, kind="subject")


# ---------------------------------------------------------------------------
# T7 — NATS subject wildcard pattern → exists=True for matching subject
# ---------------------------------------------------------------------------


def test_subject_wildcard_match(tmp_path: Path) -> None:
    """A subject matching a wildcard pattern in ACL resolves as existing."""
    _acl_matrix(tmp_path, ["lyra.turns.>"])
    inv = CodeInventory.build(tmp_path)
    # lyra.turns.write matches lyra.turns.>
    v = inv.resolve("lyra.turns.write")
    assert v == Verdict(exists=True, kind="subject")


def test_subject_star_wildcard(tmp_path: Path) -> None:
    """A token matching a * pattern resolves as existing subject."""
    _acl_matrix(tmp_path, ["lyra.voice.tts.request.*"])
    inv = CodeInventory.build(tmp_path)
    v = inv.resolve("lyra.voice.tts.request.worker1")
    assert v == Verdict(exists=True, kind="subject")


# ---------------------------------------------------------------------------
# T8 — NATS subject not in set → exists=False, kind=subject
# ---------------------------------------------------------------------------


def test_subject_dead(tmp_path: Path) -> None:
    """A lyra.* token not in the subjects set resolves as dead subject."""
    _acl_matrix(tmp_path, ["lyra.turns.write"])
    inv = CodeInventory.build(tmp_path)
    v = inv.resolve("lyra.ghost.nonexistent")
    assert v.exists is False
    assert v.kind == "subject"


# ---------------------------------------------------------------------------
# T9 — subject NOT mistaken for module
# ---------------------------------------------------------------------------


def test_subject_not_mistaken_for_dead_module(tmp_path: Path) -> None:
    """lyra.clipool.cmd (known subject) does not appear as a dead module."""
    _make_src_module(tmp_path, "lyra/__init__.py")
    _acl_matrix(tmp_path, ["lyra.clipool.cmd"])
    inv = CodeInventory.build(tmp_path)
    v = inv.resolve("lyra.clipool.cmd")
    assert v == Verdict(exists=True, kind="subject")


# ---------------------------------------------------------------------------
# T10 — packages/ layout resolves module names correctly
# ---------------------------------------------------------------------------


def test_package_module_live(tmp_path: Path) -> None:
    """A .py file under packages/<pkg>/src/ resolves as an existing module."""
    _make_pkg_module(
        tmp_path, "roxabi-nats", "roxabi_nats/connect.py", "def connect(): ...\n"
    )
    inv = CodeInventory.build(tmp_path)
    v = inv.resolve("roxabi_nats.connect")
    assert v == Verdict(exists=True, kind="module")


# ---------------------------------------------------------------------------
# T11 — src/ path exists → kind=path, exists=True
# ---------------------------------------------------------------------------


def test_path_live(tmp_path: Path) -> None:
    """A src/ path that exists on disk resolves as existing path."""
    _make_src_module(tmp_path, "lyra/core/hub.py")
    inv = CodeInventory.build(tmp_path)
    v = inv.resolve("src/lyra/core/hub.py")
    assert v == Verdict(exists=True, kind="path")


def test_package_relative_path_live(tmp_path: Path) -> None:
    """src/roxabi_nats/__init__.py resolves under packages/roxabi-nats/."""
    _make_pkg_module(tmp_path, "roxabi-nats", "roxabi_nats/__init__.py", "# init\n")
    inv = CodeInventory.build(tmp_path)
    v = inv.resolve("src/roxabi_nats/__init__.py")
    assert v == Verdict(exists=True, kind="path")


# ---------------------------------------------------------------------------
# T12 — src/ path does not exist → kind=path, exists=False
# ---------------------------------------------------------------------------


def test_path_dead(tmp_path: Path) -> None:
    """A src/ path that does not exist resolves as dead path."""
    inv = CodeInventory.build(tmp_path)
    v = inv.resolve("src/lyra/core/ghost_file.py")
    assert v == Verdict(exists=False, kind="path")


# ---------------------------------------------------------------------------
# T13 — directory traversal rejected
# ---------------------------------------------------------------------------


def test_path_traversal_rejected(tmp_path: Path) -> None:
    """A token with .. path traversal is rejected as exists=False."""
    inv = CodeInventory.build(tmp_path)
    v = inv.resolve("src/../../../etc/passwd")
    # kind=path, but exists=False (traversal guard)
    assert v.exists is False


# ---------------------------------------------------------------------------
# T14 — builtin name → exists=True
# ---------------------------------------------------------------------------


def test_builtin_name_lives(tmp_path: Path) -> None:
    """A Python builtin name (str, int, list) resolves as existing symbol."""
    inv = CodeInventory.build(tmp_path)
    for name in ("str", "int", "list", "dict", "None", "True"):
        v = inv.resolve(name)
        assert v == Verdict(exists=True, kind="symbol"), (
            f"builtin {name!r} should exist"
        )


# ---------------------------------------------------------------------------
# T15 — imported name (re-exported external) resolves as existing symbol
# ---------------------------------------------------------------------------


def test_imported_name_lives(tmp_path: Path) -> None:
    """A name imported at module level resolves as an existing symbol."""
    _make_src_module(
        tmp_path,
        "lyra/core/hub.py",
        "from fastapi import FastAPI\nclass Hub: ...\n",
    )
    inv = CodeInventory.build(tmp_path)
    # FastAPI is imported, so it's in symbols
    v = inv.resolve("FastAPI")
    assert v == Verdict(exists=True, kind="symbol")


# ---------------------------------------------------------------------------
# T16 — external / genuinely third-party → kind=unknown
# ---------------------------------------------------------------------------


def test_external_name_is_unknown(tmp_path: Path) -> None:
    """A non-project, non-builtin name resolves as kind=unknown (no false positive)."""
    inv = CodeInventory.build(tmp_path)
    # 'requests' is a third-party library, not a PascalCase class
    v = inv.resolve("requests")
    assert v.kind == "unknown"


# ---------------------------------------------------------------------------
# T17 — template / glob tokens → kind=unknown (no false positive)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    [
        "lyra.inbound.<platform>.<bot_id>",
        "lyra.{domain}.*",
        "src/**/*.py",
        "src/lyra/adapters/{platform}/{platform}_inbound.py",
        "roxabi_nats.serialize()",
        "roxabi_contracts.voice|image",
        "src/lyra/core/messaging/message.py:160",
        "src/lyra/bootstrap/factory/agent_factory.py::_build_per_agent_registry()",
        "lyra.*",
        "lyra.outbound.*",
    ],
)
def test_template_tokens_are_unknown(tmp_path: Path, token: str) -> None:
    """Template/glob tokens must not produce false positives (kind=unknown)."""
    inv = CodeInventory.build(tmp_path)
    v = inv.resolve(token)
    assert v.kind == "unknown", (
        f"Token {token!r} should be kind=unknown, got {v.kind!r}"
    )


# ---------------------------------------------------------------------------
# T18 — syntax errors in scanned files are surfaced (not silently skipped)
# ---------------------------------------------------------------------------


def test_syntax_error_in_source_is_collected(tmp_path: Path) -> None:
    """A Python file with a SyntaxError is recorded in inventory.syntax_errors."""
    bad_file = tmp_path / "src" / "lyra" / "broken.py"
    bad_file.parent.mkdir(parents=True, exist_ok=True)
    bad_file.write_text("def oops(:\n    pass\n", encoding="utf-8")
    inv = CodeInventory.build(tmp_path)
    assert len(inv.syntax_errors) >= 1
    paths = [str(p) for p, _ in inv.syntax_errors]
    assert any("broken.py" in p for p in paths)


# ---------------------------------------------------------------------------
# T19 — _is_template_token unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,expected",
    [
        ("lyra.inbound.<platform>.<bot_id>", True),
        ("lyra.{domain}.*", True),
        ("src/**/*.py", True),
        ("lyra.outbound.*", True),
        ("lyra.*", True),
        ("roxabi_nats.serialize()", True),
        ("roxabi_contracts.voice|image", True),
        ("src/lyra/core/hub.py:160", True),
        ("src/lyra/bootstrap/factory/file.py::method()", True),
        ("lyra.clipool.cmd", False),  # real subject
        ("lyra.core.hub", False),  # real module
        ("Hub", False),  # real symbol
        ("src/lyra/core/hub.py", False),  # real path
    ],
)
def test_is_template_token(token: str, expected: bool) -> None:
    assert _is_template_token(token) is expected, (
        f"_is_template_token({token!r}) should be {expected}"
    )
