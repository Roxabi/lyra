"""Tests for roxabi_contracts.image subjects namespace.

Canonical subject strings used by lyra's NatsImageClient and the image
satellite. Lock those strings so a typo or accidental rename fails here,
not silently in production.
"""

import pytest

from roxabi_contracts.image import SUBJECTS
from roxabi_contracts.image.subjects import per_worker_image


def test_image_request_subject() -> None:
    assert SUBJECTS.image_request == "lyra.image.generate.request"


def test_image_heartbeat_subject() -> None:
    assert SUBJECTS.image_heartbeat == "lyra.image.heartbeat"


def test_queue_group_constant() -> None:
    assert SUBJECTS.image_workers == "image_workers"


def test_per_worker_helper() -> None:
    assert per_worker_image("w1") == "lyra.image.generate.request.w1"


_UNSAFE_WORKER_IDS = [
    ("*", "wildcard-star"),
    (">", "wildcard-subtree"),
    ("a.b", "single-dot"),
    ("worker.nested", "double-token"),
    ("", "empty"),
    ("with space", "space"),
    ("w/slash", "slash"),
    ("w\x00null", "null-byte"),
]


@pytest.mark.parametrize(
    "bad_id",
    [bad for bad, _ in _UNSAFE_WORKER_IDS],
    ids=[label for _, label in _UNSAFE_WORKER_IDS],
)
def test_per_worker_image_rejects_unsafe_worker_id(bad_id: str) -> None:
    """NATS subject injection guard — see subjects.py::validate_worker_id."""
    with pytest.raises(ValueError, match="[A-Za-z0-9_-]"):
        per_worker_image(bad_id)


def test_image_init_does_not_pull_nats_transports() -> None:
    """Package surface: image/__init__.py imports no transport code.

    Fresh subprocess import of ``roxabi_contracts.image`` must not load
    any ``nats.*`` or ``roxabi_nats.*`` module. Guards the pure-Pydantic
    invariant from a silent regression where a stray transport import
    sneaks into models.py or subjects.py.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import roxabi_contracts.image, sys; "
                "bad = [m for m in sys.modules if m.startswith('nats') or "
                "m.startswith('roxabi_nats')]; "
                "assert not bad, f'transport modules leaked: {bad!r}'"
            ),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_image_init_does_not_expose_fixtures() -> None:
    """Fixtures are test-only — must NOT be in image package surface."""
    import subprocess
    import sys

    import roxabi_contracts.image as image_mod

    assert "fixtures" not in image_mod.__all__

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import roxabi_contracts.image, sys; "
                "assert 'roxabi_contracts.image.fixtures' not in sys.modules, "
                "'image/__init__.py must not import fixtures'"
            ),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
