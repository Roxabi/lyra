"""Test-only entry point: runs gen_nkeys with the fake nkey provider pre-seeded.
Lives under tests/ so production scripts never import a fake. (#1093)"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import importlib.util as _ilu  # noqa: E402

import scripts._modes as _modes  # noqa: E402
from scripts.gen_nkeys import main  # noqa: E402

# Import FakeNkeyProvider directly from its module file to avoid executing
# tests/fakes/__init__.py, which transitively imports lyra (not installed in
# this subprocess environment).
_spec = _ilu.spec_from_file_location(
    "tests.fakes.nkey_provider",
    _REPO_ROOT / "tests" / "fakes" / "nkey_provider.py",
)
_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
FakeNkeyProvider = _mod.FakeNkeyProvider

_modes._provider_factory = FakeNkeyProvider

if __name__ == "__main__":
    main()
