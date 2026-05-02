"""Shared fixtures and helpers for tests/scripts — #1017 gen_nkeys.py."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from scripts._acl_models import LoadedMatrix
from scripts._nk import FakeNkeyProvider as FakeNkeyProvider  # noqa: F401

# ── Repo root ────────────────────────────────────────────────────────────────
REPO = Path(__file__).resolve().parents[2]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# ── Fixture JSON paths ────────────────────────────────────────────────────────
_V2_PROD_JSON = FIXTURES_DIR / "v2-prod.json"
_V1_LEGACY_JSON = FIXTURES_DIR / "v1-legacy.json"
_V2_WITH_RETIRED_JSON = FIXTURES_DIR / "v2-with-retired.json"
_REAL_MATRIX_JSON = REPO / "deploy" / "nats" / "acl-matrix.json"


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
    """Raw JSON of deploy/nats/acl-matrix.json."""
    return json.loads(_REAL_MATRIX_JSON.read_text())


@pytest.fixture()
def legacy_matrix() -> LoadedMatrix:
    """Raw JSON of tests/scripts/fixtures/v1-legacy.json."""
    return json.loads(_V1_LEGACY_JSON.read_text())


@pytest.fixture()
def with_retired_matrix() -> LoadedMatrix:
    """Raw JSON of tests/scripts/fixtures/v2-with-retired.json."""
    return json.loads(_V2_WITH_RETIRED_JSON.read_text())
