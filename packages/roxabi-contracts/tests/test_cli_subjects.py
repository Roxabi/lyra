"""Tests locking CliPool-domain subject strings."""

from roxabi_contracts.cli import SUBJECTS


def test_cmd_subject() -> None:
    assert SUBJECTS.cmd == "factory.jobs.claude"


def test_control_subject() -> None:
    assert SUBJECTS.control == "factory.clipool.control"


def test_heartbeat_subject() -> None:
    assert SUBJECTS.heartbeat == "factory.clipool.heartbeat"


def test_queue_group_constant() -> None:
    assert SUBJECTS.clipool_workers == "clipool-workers"
