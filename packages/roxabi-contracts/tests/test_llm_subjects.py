"""Tests locking LLM-domain subject strings."""

import pytest

from roxabi_contracts.llm import SUBJECTS
from roxabi_contracts.llm.subjects import per_worker_llm


def test_generate_request_subject() -> None:
    assert SUBJECTS.generate_request == "lyra.llm.generate.request"


def test_heartbeat_subject() -> None:
    assert SUBJECTS.heartbeat == "lyra.llm.heartbeat"


def test_queue_group_constant() -> None:
    assert SUBJECTS.llm_workers == "llm-workers"


def test_per_worker_helper() -> None:
    assert per_worker_llm("w1") == "lyra.llm.generate.request.w1"


def test_per_worker_rejects_dot() -> None:
    with pytest.raises(ValueError, match="worker_id"):
        per_worker_llm("bad.id")


def test_per_worker_rejects_wildcard() -> None:
    with pytest.raises(ValueError, match="worker_id"):
        per_worker_llm("bad*")
