"""Tests for roxabi_contracts.socialmedia subjects."""

from __future__ import annotations

import pytest

from roxabi_contracts.socialmedia import SUBJECTS, per_worker_socialmedia


def test_subject_literals() -> None:
    assert SUBJECTS.publish == "factory.tool.socialmedia.publish"
    assert SUBJECTS.heartbeat == "factory.tool.socialmedia.heartbeat"


def test_per_worker_socialmedia() -> None:
    assert (
        per_worker_socialmedia("adapter-1", "publish")
        == "factory.tool.socialmedia.publish.adapter-1"
    )


def test_per_worker_unknown_action() -> None:
    with pytest.raises(ValueError, match="unknown socialmedia action"):
        per_worker_socialmedia("adapter-1", "delete")