"""Shared markers for deploy contract tests."""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "/tests/deploy/" not in str(item.fspath):
            continue
        item.add_marker(pytest.mark.deploy_contract)
