"""JSON schema validation for layout.json.

Loads layout.schema.json from the package root (scripts/dep-graph/).
Falls back to inline schema if the file is missing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCHEMA_FILE = Path(__file__).parent.parent / "layout.schema.json"


def validate_layout(layout: dict, *, verbose: bool = False) -> bool:
    """Validate layout dict against layout.schema.json.

    Returns True on success, False on failure (errors printed to stderr).
    Requires jsonschema; emits a clear error if not installed.
    """
    try:
        import jsonschema  # type: ignore[import-untyped]
    except ImportError:
        print(
            "ERROR: jsonschema not installed. Run: uv add jsonschema --dev",
            file=sys.stderr,
        )
        return False

    if not SCHEMA_FILE.exists():
        print(f"ERROR: schema file not found: {SCHEMA_FILE}", file=sys.stderr)
        return False

    schema = json.loads(SCHEMA_FILE.read_text())

    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(layout), key=lambda e: list(e.absolute_path))

    if not errors:
        if verbose:
            print("Schema validation passed.")
        return True

    for err in errors:
        path = " → ".join(str(p) for p in err.absolute_path) or "(root)"
        print(f"  SCHEMA ERROR at {path}: {err.message}", file=sys.stderr)
    return False
