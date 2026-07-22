"""Tests locking memory-domain subject strings (ADR-087)."""

from roxabi_contracts.memory import SUBJECTS


def test_assemble_subject() -> None:
    assert SUBJECTS.query_assemble == "roxabi.memory.query.assemble"


def test_search_subject() -> None:
    assert SUBJECTS.query_search == "roxabi.memory.query.search"


def test_capture_subject() -> None:
    assert SUBJECTS.capture == "roxabi.memory.capture"


def test_heartbeat_subject() -> None:
    assert SUBJECTS.heartbeat == "roxabi.memory.heartbeat"


def test_queue_group() -> None:
    assert SUBJECTS.workers == "memory-workers"


def test_namespace_is_roxabi_not_factory() -> None:
    """Cross-project peer — must not use factory.* prefix (ADR-087)."""
    for attr in ("query_assemble", "query_search", "capture", "heartbeat"):
        value = getattr(SUBJECTS, attr)
        assert value.startswith("roxabi.memory."), value
        assert not value.startswith("factory."), value
