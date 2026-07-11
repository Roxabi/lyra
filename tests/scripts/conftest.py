"""Shared fixtures and helpers for tests/scripts — #1017 gen_nkeys.py."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from scripts._acl_models import LoadedMatrix

from tests.fakes.nkey_provider import FakeNkeyProvider as FakeNkeyProvider  # noqa: F401

# ── Repo root ────────────────────────────────────────────────────────────────
REPO = Path(__file__).resolve().parents[2]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# ── Fixture JSON paths ────────────────────────────────────────────────────────
_V2_PROD_JSON = FIXTURES_DIR / "v2-prod.json"
_V1_LEGACY_JSON = FIXTURES_DIR / "v1-legacy.json"
_V2_WITH_RETIRED_JSON = FIXTURES_DIR / "v2-with-retired.json"
_REAL_MATRIX_JSON = REPO / "deploy" / "nats" / "acl-matrix.json"

# Only suites that shell out to the nk binary — genkeys_modes* use FakeNkeyProvider.
_NK_TOOL_MODULES = frozenset({"test_nk.py"})


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "/tests/scripts/" not in str(item.fspath):
            continue
        if Path(item.fspath).name not in _NK_TOOL_MODULES:
            continue
        item.add_marker(pytest.mark.nk_tooling)


_live_acl_ci_guard_checked = False


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Hard-fail before the first live_acl test when CI infra deps are absent.

    Runs only for tests that survive the active ``-m`` filter, so the ``tests``
    job (``not live_acl``) imports ``test_parity_e2e`` without nats-server/nk
    while the ``infra`` job still fails loudly on a broken runner (#2247).
    """
    global _live_acl_ci_guard_checked
    if _live_acl_ci_guard_checked or item.get_closest_marker("live_acl") is None:
        return
    _live_acl_ci_guard_checked = True

    from tests.scripts.test_parity_e2e import (
        NATS_AVAILABLE,
        NATS_PY_AVAILABLE,
        NK_AVAILABLE,
        _ci_hardfail_missing_deps,
    )

    missing = _ci_hardfail_missing_deps(
        github_actions=os.getenv("GITHUB_ACTIONS") == "true",
        nats_available=NATS_AVAILABLE,
        nk_available=NK_AVAILABLE,
        nats_py_available=NATS_PY_AVAILABLE,
    )
    if missing:
        pytest.fail(
            f"GITHUB_ACTIONS=true but missing: {', '.join(missing)} — "
            "CI must install nats-server + nk and have nats-py importable; a "
            "silent skip here would mask a broken CI environment (#2247)."
        )


# ── Path fixtures (copies to tmp_path for isolation) ─────────────────────────


@pytest.fixture()
def prod_matrix_path(tmp_path: Path) -> Path:
    """Path to a tmp copy of deploy/nats/acl-matrix.json."""
    dest = tmp_path / "acl-matrix.json"
    shutil.copy2(_REAL_MATRIX_JSON, dest)
    return dest


@pytest.fixture()
def legacy_matrix_path(tmp_path: Path) -> Path:
    """Path to a tmp copy of tests/scripts/fixtures/v1-legacy.json."""
    dest = tmp_path / "v1-legacy.json"
    shutil.copy2(_V1_LEGACY_JSON, dest)
    return dest


@pytest.fixture()
def with_retired_matrix_path(tmp_path: Path) -> Path:
    """Path to a tmp copy of tests/scripts/fixtures/v2-with-retired.json."""
    dest = tmp_path / "v2-with-retired.json"
    shutil.copy2(_V2_WITH_RETIRED_JSON, dest)
    return dest


# ── In-memory dict fixtures (raw JSON loads, LoadedMatrix-shaped) ─────────────


@pytest.fixture()
def prod_matrix() -> LoadedMatrix:
    """Validated LoadedMatrix from deploy/nats/acl-matrix.json."""
    from scripts._loader import load_matrix

    return load_matrix(_REAL_MATRIX_JSON)


@pytest.fixture()
def legacy_matrix() -> LoadedMatrix:
    """Validated LoadedMatrix from tests/scripts/fixtures/v1-legacy.json."""
    from scripts._loader import load_matrix

    return load_matrix(_V1_LEGACY_JSON)


@pytest.fixture()
def with_retired_matrix() -> LoadedMatrix:
    """Validated LoadedMatrix from tests/scripts/fixtures/v2-with-retired.json."""
    from scripts._loader import load_matrix

    return load_matrix(_V2_WITH_RETIRED_JSON)
