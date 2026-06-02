"""Guard: scripts/ modules must not import from factory.*"""

import ast
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


def _get_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


def test_no_lyra_imports_in_scripts() -> None:
    """Guard: scripts/* must not import from factory.*"""
    violations: list[str] = []
    for py_file in SCRIPTS_DIR.glob("*.py"):
        if py_file.name.startswith("_") and py_file.name == "__init__.py":
            continue
        for imp in _get_imports(py_file):
            if imp.startswith("lyra"):
                violations.append(f"{py_file.name}: imports {imp!r}")
    assert not violations, "Forbidden lyra imports found:\n" + "\n".join(violations)
