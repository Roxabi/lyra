#!/usr/bin/env python3
"""Semantic oracle for codebase symbol/module/subject resolution.

Provides CodeInventory.build() which performs a single AST-based pass over
src/**/*.py and packages/*/src/**/*.py to collect:
  - modules: set of dotted module names (lyra.core.hub, roxabi_nats.connect, …)
  - symbols: dict mapping bare name → set of defining module paths
  - subjects: frozenset of NATS subject literals and wildcard patterns

Used by check_doc_drift.py to replace textual regex guessing with semantic
resolution.  Stdlib-only: ast, builtins, json, pathlib, re.

Exit-code contract for build() callers: if syntax_errors is non-empty, the
caller should exit 2 (script broke — scanned source has parse errors).
"""

from __future__ import annotations

import ast
import builtins
import json
import re
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

Kind = Literal["module", "symbol", "subject", "path", "unknown"]


class Verdict:
    """Resolution result for a single token."""

    __slots__ = ("exists", "kind")

    def __init__(self, exists: bool, kind: Kind) -> None:
        self.exists = exists
        self.kind = kind

    def __repr__(self) -> str:
        return f"Verdict(exists={self.exists!r}, kind={self.kind!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Verdict):
            return NotImplemented
        return self.exists == other.exists and self.kind == other.kind


# ---------------------------------------------------------------------------
# Project namespace configuration
# ---------------------------------------------------------------------------

# Dotted-name prefixes owned by this project.  Other dotted tokens are
# treated as external → kind=unknown (no false positive).
_PROJECT_PREFIXES: frozenset[str] = frozenset(
    ["lyra", "roxabi_nats", "roxabi_contracts", "roxabi_blobs", "roxabi_vault"]
)

# Well-known external / generic PascalCase names that appear in project docs
# but are NOT project-defined classes.  The oracle skips these (kind=unknown)
# to avoid false positives.  Only add entries confirmed as external-only.
_EXTERNAL_KNOWN_NAMES: frozenset[str] = frozenset(
    [
        # stdlib exceptions and warnings
        "KeyError", "ValueError", "TypeError", "RuntimeError", "OSError",
        "AttributeError", "NotImplementedError", "StopAsyncIteration",
        "DeprecationWarning", "UserWarning", "StopIteration", "Exception",
        "BaseException", "ImportError", "FileNotFoundError", "PermissionError",
        "TimeoutError", "ConnectionError", "OverflowError", "IndexError",
        "NameError", "UnicodeDecodeError", "UnicodeEncodeError",
        # typing / generic terms
        "PascalCase", "CamelCase", "TypeVar", "Protocol", "Optional", "Union",
        "Dict", "List", "Tuple", "Set", "Any", "Callable", "Iterator",
        "AsyncIterator", "Generator", "AsyncGenerator",
        # framework / external library names
        "GitHub", "Discord", "Telegram", "FastAPI", "Pydantic",
        "Python", "MagicMock", "AsyncMock",
        # nats-py external exceptions
        "NoRespondersError", "BucketNotFoundError",
        # anthropic / LLM library types
        "InputJsonDelta",
        # HTTP/ASGI transports
        "ASGITransport", "HTTPTransport",
        # generic doc terms that are not project classes
        "RunError",
    ]
)

# ---------------------------------------------------------------------------
# NATS wildcard matching
# ---------------------------------------------------------------------------


def _nats_matches(pattern: str, subject: str) -> bool:
    """Return True if *subject* matches NATS wildcard *pattern*.

    NATS wildcards:
      *  — matches exactly one token (no dots allowed in that token)
      >  — matches one or more tokens at the end; must be the last token
    """
    p_tokens = pattern.split(".")
    s_tokens = subject.split(".")
    pi = 0
    si = 0
    while pi < len(p_tokens) and si < len(s_tokens):
        pt = p_tokens[pi]
        if pt == ">":
            # > must be last and matches all remaining subject tokens (≥1)
            return si < len(s_tokens)
        if pt == "*":
            pi += 1
            si += 1
        elif pt == s_tokens[si]:
            pi += 1
            si += 1
        else:
            return False
    return pi == len(p_tokens) and si == len(s_tokens)


def _subject_matches_any(token: str, subjects: frozenset[str]) -> bool:
    """Return True if *token* is in *subjects* or matches a wildcard pattern."""
    if token in subjects:
        return True
    for pat in subjects:
        if ("*" in pat or ">" in pat) and _nats_matches(pat, token):
            return True
    return False


# ---------------------------------------------------------------------------
# Module-name derivation
# ---------------------------------------------------------------------------


def _module_name_from_path(py_file: Path, src_root: Path) -> str | None:
    """Derive dotted module name from *py_file* relative to *src_root*.

    Returns None if the file is not under src_root or is not a .py file.
    """
    try:
        rel = py_file.relative_to(src_root)
    except ValueError:
        return None
    parts = list(rel.parts)
    if not parts:
        return None
    last = parts[-1]
    if not last.endswith(".py"):
        return None
    parts[-1] = last[:-3]  # strip .py
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return None
    return ".".join(parts)


# ---------------------------------------------------------------------------
# AST collection helpers
# ---------------------------------------------------------------------------


def _collect_top_level_names(tree: ast.Module) -> set[str]:
    """Return names defined at module top level (classes, functions, assignments)."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


def _collect_import_names(tree: ast.Module) -> set[str]:
    """Return names introduced by import statements at module level."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                eff = alias.asname if alias.asname else alias.name.split(".")[-1]
                names.add(eff)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                eff = alias.asname if alias.asname else alias.name
                names.add(eff)
    return names


# ---------------------------------------------------------------------------
# Template-token detection
# ---------------------------------------------------------------------------


def _is_template_token(token: str) -> bool:
    """Return True if *token* is a documentation template/pattern, not a ref.

    Templates are skipped (kind=unknown) to avoid false positives.
    See _is_template_by_char and _is_template_by_shape for the two groups.
    """
    return _is_template_by_char(token) or _is_template_by_shape(token)


def _is_template_by_char(token: str) -> bool:
    """Return True if token contains documentation-notation characters."""
    # <...> / {...} → placeholder; | → alternation; () → call; :: → annotation
    return (
        "<" in token
        or "{" in token
        or "|" in token
        or "(" in token
        or ")" in token
        or "::" in token
    )


def _is_template_by_shape(token: str) -> bool:
    """Return True if token has a glob/wildcard shape that marks it as a pattern."""
    # Path tokens with shell glob chars or line-range annotation
    if token.startswith(("src/", "packages/")):
        if "*" in token or "?" in token:
            return True
        if re.search(r":\d", token):
            return True
        return False
    # Non-path tokens with slash (lyra.outbound/, lyra.adapters/__init__.py)
    if "/" in token:
        return True
    # NATS namespace doc-patterns: lyra.* or lyra.something.*
    if token == "lyra.*" or re.match(r"^lyra\.[a-z_.]+\.\*$", token):
        return True
    # Any non-path token containing * (lyra.nats.*_client)
    if "*" in token:
        return True
    return False


# ---------------------------------------------------------------------------
# Subject extraction
# ---------------------------------------------------------------------------


def _looks_like_nats_subject(val: str) -> bool:
    """Heuristic: does *val* look like a NATS subject literal?"""
    if not val or " " in val or "\n" in val or len(val) > 200:
        return False
    lower = val.lower()
    return (
        lower.startswith("lyra.")
        or lower.startswith("$js.")
        or lower.startswith("$kv.")
        or lower.startswith("_inbox.")
    )


def _extract_subjects_from_contracts(contracts_src: Path) -> set[str]:
    """Walk roxabi-contracts src AST and collect NATS subject string constants."""
    subjects: set[str] = set()
    for py_file in contracts_src.rglob("*.py"):
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _looks_like_nats_subject(node.value):
                    subjects.add(node.value)
    return subjects


def _extract_subjects_from_acl(acl_path: Path) -> set[str]:
    """Parse acl-matrix.json and return all publish/subscribe subject literals."""
    subjects: set[str] = set()
    if not acl_path.exists():
        return subjects
    try:
        data = json.loads(acl_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return subjects

    for flow in data.get("request_reply_flows", []):
        subj = flow.get("subject", "")
        if subj and isinstance(subj, str):
            subjects.add(subj)

    for identity in data.get("identities", {}).values():
        for entry in identity.get("publish", []):
            if isinstance(entry, str):
                subjects.add(entry)
        for entry in identity.get("subscribe", []):
            if isinstance(entry, str):
                subjects.add(entry)

    return subjects


# ---------------------------------------------------------------------------
# Build helpers (extracted to keep build() below complexity threshold)
# ---------------------------------------------------------------------------


def _discover_src_roots(root: Path) -> list[Path]:
    """Return all src roots to scan: root/src plus packages/*/src."""
    roots: list[Path] = []
    main_src = root / "src"
    if main_src.is_dir():
        roots.append(main_src)
    pkg_root = root / "packages"
    if pkg_root.is_dir():
        for pkg_dir in sorted(pkg_root.iterdir()):
            pkg_src = pkg_dir / "src"
            if pkg_src.is_dir():
                roots.append(pkg_src)
    return roots


def _ast_pass(
    src_roots: list[Path],
) -> tuple[set[str], dict[str, set[str]], list[tuple[Path, str]]]:
    """Walk src_roots with AST, collecting modules, symbols, syntax errors."""
    modules: set[str] = set()
    symbols: dict[str, set[str]] = {}
    syntax_errors: list[tuple[Path, str]] = []

    for src_root in src_roots:
        for py_file in sorted(src_root.rglob("*.py")):
            mod_name = _module_name_from_path(py_file, src_root)
            if mod_name is None:
                continue
            modules.add(mod_name)
            try:
                source = py_file.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError as exc:
                syntax_errors.append((py_file, str(exc)))
                continue
            top_names = _collect_top_level_names(tree)
            import_names = _collect_import_names(tree)
            for name in top_names | import_names:
                if not name:
                    continue
                if name not in symbols:
                    symbols[name] = set()
                symbols[name].add(mod_name)

    return modules, symbols, syntax_errors


def _collect_subjects(root: Path) -> frozenset[str]:
    """Collect all NATS subjects from ACL matrix and contracts."""
    all_subjects: set[str] = set()
    acl_path = root / "deploy" / "nats" / "acl-matrix.json"
    all_subjects |= _extract_subjects_from_acl(acl_path)
    pkg_root = root / "packages"
    if pkg_root.is_dir():
        contracts_src = pkg_root / "roxabi-contracts" / "src"
        if contracts_src.is_dir():
            all_subjects |= _extract_subjects_from_contracts(contracts_src)
    return frozenset(all_subjects)


# ---------------------------------------------------------------------------
# CodeInventory
# ---------------------------------------------------------------------------


class CodeInventory:
    """Semantic oracle: modules, symbols, NATS subjects from a codebase scan.

    Build once with CodeInventory.build(root), then call resolve(token) for
    each backtick token found in documentation.
    """

    def __init__(  # noqa: PLR0913
        self,
        root: Path,
        modules: set[str],
        symbols: dict[str, set[str]],
        subjects: frozenset[str],
        builtin_names: frozenset[str],
        syntax_errors: list[tuple[Path, str]],
    ) -> None:
        self._root = root
        self.modules = modules
        self.symbols = symbols
        self.subjects = subjects
        self._builtins = builtin_names
        self.syntax_errors = syntax_errors

    # ------------------------------------------------------------------ build

    @classmethod
    def build(cls, root: Path) -> "CodeInventory":
        """Build a CodeInventory in a single pass over root.

        Scans:
          - src/**/*.py  (if src/ exists)
          - packages/*/src/**/*.py  (for each package under packages/)
          - deploy/nats/acl-matrix.json  (NATS subject literals)
          - packages/roxabi-contracts/src/**/*.py  (NATS subject constants)

        Files with SyntaxError are recorded in .syntax_errors; the caller
        should exit 2 if syntax_errors is non-empty.
        """
        src_roots = _discover_src_roots(root)
        modules, symbols, syntax_errors = _ast_pass(src_roots)
        subjects = _collect_subjects(root)
        return cls(
            root=root,
            modules=modules,
            symbols=symbols,
            subjects=subjects,
            builtin_names=frozenset(dir(builtins)),
            syntax_errors=syntax_errors,
        )

    # ------------------------------------------------------------------ resolve

    def resolve(self, token: str) -> Verdict:
        """Resolve a single backtick token to a Verdict.

        Resolution order:
          1. Template/glob tokens → kind=unknown (no false positive)
          2. Starts with src/ or packages/ → path resolution
          3. Contains a dot → module/qualified-symbol/subject resolution
          4. Bare word → builtin / project symbol / PascalCase dead ref
          5. Genuinely external → kind=unknown
        """
        t = token.strip()
        if not t:
            return Verdict(exists=False, kind="unknown")

        # 1. Template/glob tokens (documentation patterns, not real refs)
        if _is_template_token(t):
            return Verdict(exists=False, kind="unknown")

        # 2. Path tokens
        if t.startswith(("src/", "packages/")):
            return self._resolve_path(t)

        # 3. Dotted token: module, qualified symbol, or NATS subject
        if "." in t:
            return self._resolve_dotted_with_subjects(t)

        # 4. Bare NATS subject (exact match only, no dots)
        if self._looks_like_subject_token(t):
            exists = _subject_matches_any(t, self.subjects)
            return Verdict(exists=exists, kind="subject")

        # 5. Bare word
        return self._resolve_bare(t)

    # ------------------------------------------------------------------ internals

    def _resolve_path(self, token: str) -> Verdict:
        """Resolve src/... or packages/... path token.

        Guards against directory traversal (rejects tokens containing ..).
        Also tries package-relative: a token like src/roxabi_nats/__init__.py
        may live at packages/roxabi-nats/src/roxabi_nats/__init__.py.
        """
        if ".." in token.split("/"):
            return Verdict(exists=False, kind="path")

        # Root-relative
        candidate = self._root / token
        try:
            resolved = candidate.resolve()
            resolved.relative_to(self._root.resolve())
            if resolved.exists():
                return Verdict(exists=True, kind="path")
        except (ValueError, OSError):
            pass

        # Package-relative: try packages/<pkg>/<token> for each package
        if token.startswith("src/"):
            pkg_root = self._root / "packages"
            if pkg_root.is_dir():
                for pkg_dir in pkg_root.iterdir():
                    candidate = pkg_dir / token
                    try:
                        resolved = candidate.resolve()
                        resolved.relative_to(self._root.resolve())
                        if resolved.exists():
                            return Verdict(exists=True, kind="path")
                    except (ValueError, OSError):
                        continue

        return Verdict(exists=False, kind="path")

    def _looks_like_subject_token(self, token: str) -> bool:
        """Return True if token looks like a NATS subject (no dots, project-ns)."""
        lower = token.lower()
        if any(lower.startswith(p) for p in ("lyra.", "$js.", "$kv.", "_inbox.")):
            return True
        return token in self.subjects

    def _resolve_symbol_at_split(
        self, token: str, parts: list[str]
    ) -> Verdict | None:
        """Try each split point for a module.Symbol pattern.

        Returns a Verdict when confident, or None to fall through to subject check.
        """
        for split in range(len(parts) - 1, 0, -1):
            prefix = ".".join(parts[:split])
            suffix_parts = parts[split:]
            suffix = ".".join(suffix_parts)
            if prefix not in self.modules:
                continue
            # prefix is a valid module
            if suffix in self.symbols:
                return Verdict(exists=True, kind="symbol")
            # Single uppercase-initial suffix → class reference
            if (
                len(suffix_parts) == 1
                and suffix_parts[0]
                and suffix_parts[0][0].isupper()
            ):
                if _subject_matches_any(token, self.subjects):
                    return Verdict(exists=True, kind="subject")
                return Verdict(exists=False, kind="symbol")
            # Multi-segment / lowercase suffix → fall through to subject check
            break
        return None

    def _resolve_dotted_with_subjects(self, token: str) -> Verdict:
        """Resolve a dotted token: module, symbol, subject, or dead ref.

        Priority:
          1. Direct module match → kind=module, exists=True
          2. Qualified symbol (module.Symbol) → kind=symbol
          3. NATS subject (lyra.*, $JS.*, $KV.*, _inbox.*) → kind=subject
          4. Project-namespace root not found → kind=module, exists=False
          5. External / unknown → kind=unknown
        """
        # 1. Direct module match
        if token in self.modules:
            return Verdict(exists=True, kind="module")

        parts = token.split(".")

        # 2. Qualified symbol via split-point scan
        symbol_verdict = self._resolve_symbol_at_split(token, parts)
        if symbol_verdict is not None:
            return symbol_verdict

        # Also check: last-dot prefix is a module and final segment is a symbol
        last = parts[-1]
        mod_prefix = ".".join(parts[:-1])
        if mod_prefix in self.modules:
            if last in self.symbols:
                return Verdict(exists=True, kind="symbol")
            # lowercase final segment — fall through to subject check

        # 3. NATS subject check (before declaring project token dead)
        if _subject_matches_any(token, self.subjects):
            return Verdict(exists=True, kind="subject")
        # Subject-namespace token not in subjects set → dead subject reference
        lower = token.lower()
        if any(lower.startswith(p) for p in ("lyra.", "$js.", "$kv.", "_inbox.")):
            if parts[0] not in _PROJECT_PREFIXES:
                return Verdict(exists=False, kind="unknown")
            return Verdict(exists=False, kind="subject")

        # 4. Project namespace root not found as module or subject
        if parts[0] in _PROJECT_PREFIXES:
            return Verdict(exists=False, kind="module")

        # 5. External / unknown
        return Verdict(exists=False, kind="unknown")

    def _resolve_bare(self, token: str) -> Verdict:
        """Resolve a bare word (no dots).

        Only flags as dead symbol when confident this is a project-owned class.
        External symbols, ALL_CAPS constants, and known third-party names
        resolve as kind=unknown (no false positive).
        """
        # Python builtins always live
        if token in self._builtins:
            return Verdict(exists=True, kind="symbol")

        # Known project or imported symbol
        if token in self.symbols:
            return Verdict(exists=True, kind="symbol")

        # ALL_CAPS tokens: HTTP verbs, Unix signals, config flags, enum values
        if re.match(r"^[A-Z][A-Z0-9_]+$", token):
            return Verdict(exists=False, kind="unknown")

        # PascalCase (mixed case, no underscores) → project-class candidate
        if re.match(r"^[A-Z][a-zA-Z0-9]*[a-z][a-zA-Z0-9]*$", token):
            if token in _EXTERNAL_KNOWN_NAMES:
                return Verdict(exists=False, kind="unknown")
            return Verdict(exists=False, kind="symbol")

        # Genuinely external or non-classifiable
        return Verdict(exists=False, kind="unknown")
