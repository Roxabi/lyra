"""Shared pytest markers for roxabi-contracts tests.

Kept out of ``conftest.py`` so it can be imported by test modules. Tests in
this package are intentionally not a Python package (no ``__init__.py``) —
aligning with ``packages/roxabi-nats/tests`` prevents a conftest name
collision when both are collected from the repo-root ``pyproject.toml``
``testpaths``.
"""

from __future__ import annotations

import shutil

import pytest

requires_nats_server = pytest.mark.skipif(
    shutil.which("nats-server") is None,
    reason="nats-server not found in PATH — install via 'make nats-install'",
)
