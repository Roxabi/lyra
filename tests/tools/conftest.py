"""Shared markers for deploy/tools contract tests."""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "test_install_dry_run.py" in item.nodeid:
            item.add_marker(pytest.mark.deploy_contract)
