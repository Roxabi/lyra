"""Tests for `lyra ops verify` (issue #737)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from lyra.cli import lyra_app
from lyra.cli_ops import _expand_subject, _is_permission_error, _load_matrix

runner = CliRunner()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("lyra.outbound.telegram.>", "lyra.outbound.telegram.verify"),
        ("lyra.llm.health.*", "lyra.llm.health.verify"),
        ("lyra.foo.*.bar", "lyra.foo.verify.bar"),
        ("lyra.simple", "lyra.simple"),
        ("_INBOX.>", "_INBOX.verify"),
    ],
)
def test_expand_subject(subject: str, expected: str) -> None:
    assert _expand_subject(subject) == expected


def test_is_permission_error() -> None:
    assert _is_permission_error("Permissions Violation for Publish to lyra.foo")
    assert _is_permission_error("nats: permissions violation for publish to X")
    assert not _is_permission_error("connection lost")
    assert not _is_permission_error("subscription denied")


def _write_matrix(path: Path, identities: dict) -> None:
    path.write_text(json.dumps({"version": "1", "identities": identities}))


def test_load_matrix_missing(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="not found"):
        _load_matrix(tmp_path / "missing.json")


def test_load_matrix_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    with pytest.raises(Exception, match="not valid JSON"):
        _load_matrix(p)


def test_load_matrix_no_identities(tmp_path: Path) -> None:
    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"version": "1"}))
    with pytest.raises(Exception, match="no `identities`"):
        _load_matrix(p)


def test_load_matrix_ok(tmp_path: Path) -> None:
    p = tmp_path / "ok.json"
    _write_matrix(p, {"hub": {"publish": ["lyra.foo"], "subscribe": []}})
    out = _load_matrix(p)
    assert "hub" in out


# ---------------------------------------------------------------------------
# CLI integration with mocked NATS
# ---------------------------------------------------------------------------


class _FakeNats:
    """Stand-in NATS client. Records published subjects; never errors."""

    def __init__(self, deny_subjects: set[str] | None = None) -> None:
        self.published: list[str] = []
        self._deny = deny_subjects or set()
        self._error_cb = None

    def set_error_cb(self, cb) -> None:
        self._error_cb = cb

    async def publish(self, subject: str, data: bytes) -> None:
        self.published.append(subject)
        if subject in self._deny and self._error_cb is not None:
            await self._error_cb(
                Exception(f'nats: Permissions Violation for Publish to "{subject}"')
            )

    async def flush(self, timeout: float = 2.0) -> None:  # noqa: ARG002
        return None

    async def drain(self) -> None:
        return None

    async def close(self) -> None:
        return None


def _seed_dir(tmp_path: Path, names: list[str]) -> Path:
    d = tmp_path / "nkeys"
    d.mkdir()
    for n in names:
        seed = d / f"{n}.seed"
        seed.write_text("SUFAKEKEYDATA")
        seed.chmod(0o600)
    return d


@pytest.fixture
def matrix_two(tmp_path: Path) -> Path:
    p = tmp_path / "matrix.json"
    _write_matrix(
        p,
        {
            "hub": {
                "publish": ["lyra.outbound.telegram.>", "lyra.llm.request"],
                "subscribe": [],
            },
            "monitor": {
                "publish": ["lyra.monitor.>"],
                "subscribe": [],
            },
        },
    )
    return p


def _patched_connect(deny_per_call: list[set[str]]) -> AsyncMock:
    """Build a `nats_connect` async mock that returns one fake per call."""
    iterator = iter(deny_per_call)

    async def _factory(url, **kwargs):  # noqa: ARG001
        fake = _FakeNats(next(iterator, set()))
        fake.set_error_cb(kwargs.get("error_cb"))
        return fake

    return AsyncMock(side_effect=_factory)


def test_verify_all_pass(tmp_path: Path, matrix_two: Path) -> None:
    seeds = _seed_dir(tmp_path, ["hub", "monitor"])
    # Each identity: deny only its `lyra.verify.deny.<name>` probe.
    deny = [{"lyra.verify.deny.hub"}, {"lyra.verify.deny.monitor"}]
    with patch("lyra.cli_ops.nats.connect", _patched_connect(deny)):
        result = runner.invoke(
            lyra_app,
            [
                "ops",
                "verify",
                "--matrix",
                str(matrix_two),
                "--seeds-dir",
                str(seeds),
                "--nats-url",
                "nats://fake:4222",
            ],
        )
    assert result.exit_code == 0, result.stdout
    assert "2 identities" in result.stdout
    assert "3/3 pub checks passed" in result.stdout
    assert "2/2 deny checks passed" in result.stdout


def test_verify_pub_failure_reports_first_offender(
    tmp_path: Path, matrix_two: Path
) -> None:
    seeds = _seed_dir(tmp_path, ["hub", "monitor"])
    # Hub: deny on an *allowed* subject + the verify-deny probe.
    deny = [
        {"lyra.outbound.telegram.verify", "lyra.verify.deny.hub"},
        {"lyra.verify.deny.monitor"},
    ]
    with patch("lyra.cli_ops.nats.connect", _patched_connect(deny)):
        result = runner.invoke(
            lyra_app,
            [
                "ops",
                "verify",
                "--matrix",
                str(matrix_two),
                "--seeds-dir",
                str(seeds),
            ],
        )
    assert result.exit_code == 1
    assert "FAIL hub pub lyra.outbound.telegram.verify" in result.stdout
    # Summary line still reports counts after the FAIL row.
    assert "2/3 pub checks passed" in result.stdout
    assert "2/2 deny checks passed" in result.stdout


def test_verify_deny_failure(tmp_path: Path, matrix_two: Path) -> None:
    seeds = _seed_dir(tmp_path, ["hub", "monitor"])
    # Server *accepts* the verify-deny probe → deny check fails.
    deny = [set(), set()]
    with patch("lyra.cli_ops.nats.connect", _patched_connect(deny)):
        result = runner.invoke(
            lyra_app,
            [
                "ops",
                "verify",
                "--matrix",
                str(matrix_two),
                "--seeds-dir",
                str(seeds),
            ],
        )
    assert result.exit_code == 1
    assert "FAIL hub deny lyra.verify.deny.hub" in result.stdout


def test_verify_skips_when_seed_missing(tmp_path: Path, matrix_two: Path) -> None:
    seeds = _seed_dir(tmp_path, ["hub"])  # monitor.seed missing
    deny = [{"lyra.verify.deny.hub"}]
    with patch("lyra.cli_ops.nats.connect", _patched_connect(deny)):
        result = runner.invoke(
            lyra_app,
            [
                "ops",
                "verify",
                "--matrix",
                str(matrix_two),
                "--seeds-dir",
                str(seeds),
            ],
        )
    assert result.exit_code == 1
    assert "SKIP monitor: seed missing" in result.stdout
    assert "1 skipped" in result.stdout


def test_verify_only_filter(tmp_path: Path, matrix_two: Path) -> None:
    seeds = _seed_dir(tmp_path, ["hub", "monitor"])
    deny = [{"lyra.verify.deny.monitor"}]
    with patch("lyra.cli_ops.nats.connect", _patched_connect(deny)):
        result = runner.invoke(
            lyra_app,
            [
                "ops",
                "verify",
                "--matrix",
                str(matrix_two),
                "--seeds-dir",
                str(seeds),
                "--only",
                "monitor",
            ],
        )
    assert result.exit_code == 0
    assert "1 identities" in result.stdout


def test_verify_only_unknown_identity(tmp_path: Path, matrix_two: Path) -> None:
    seeds = _seed_dir(tmp_path, ["hub", "monitor"])
    result = runner.invoke(
        lyra_app,
        [
            "ops",
            "verify",
            "--matrix",
            str(matrix_two),
            "--seeds-dir",
            str(seeds),
            "--only",
            "ghost",
        ],
    )
    assert result.exit_code != 0
    assert "ghost" in result.output


def test_verify_handles_empty_publish_list(tmp_path: Path) -> None:
    """Identity with `publish: []` reports 0/0 pub but still runs the deny probe."""
    matrix = tmp_path / "matrix.json"
    _write_matrix(matrix, {"silent": {"publish": [], "subscribe": []}})
    seeds = _seed_dir(tmp_path, ["silent"])
    deny = [{"lyra.verify.deny.silent"}]
    with patch("lyra.cli_ops.nats.connect", _patched_connect(deny)):
        result = runner.invoke(
            lyra_app,
            [
                "ops",
                "verify",
                "--matrix",
                str(matrix),
                "--seeds-dir",
                str(seeds),
            ],
        )
    assert result.exit_code == 0, result.stdout
    assert (
        "1 identities, 0/0 pub checks passed, 1/1 deny checks passed" in result.stdout
    )


class _FakeNatsDelayed(_FakeNats):
    """Variant that fires the error_cb during flush(), not publish()."""

    def __init__(self, deny_subjects: set[str] | None = None) -> None:
        super().__init__(deny_subjects)
        self._pending: list[str] = []

    async def publish(self, subject: str, data: bytes) -> None:
        self.published.append(subject)
        if subject in self._deny:
            self._pending.append(subject)

    async def flush(self, timeout: float = 2.0) -> None:  # noqa: ARG002
        if self._error_cb is not None:
            for subj in self._pending:
                await self._error_cb(
                    Exception(f'nats: Permissions Violation for Publish to "{subj}"')
                )
        self._pending.clear()


def test_verify_handles_post_flush_error_arrival(
    tmp_path: Path, matrix_two: Path
) -> None:
    """Permission error arriving on the next event-loop tick is still caught."""
    seeds = _seed_dir(tmp_path, ["hub", "monitor"])
    deny = [{"lyra.verify.deny.hub"}, {"lyra.verify.deny.monitor"}]
    iterator = iter(deny)

    async def _factory(url, **kwargs):  # noqa: ARG001
        fake = _FakeNatsDelayed(next(iterator, set()))
        fake.set_error_cb(kwargs.get("error_cb"))
        return fake

    with patch("lyra.cli_ops.nats.connect", side_effect=_factory):
        result = runner.invoke(
            lyra_app,
            [
                "ops",
                "verify",
                "--matrix",
                str(matrix_two),
                "--seeds-dir",
                str(seeds),
            ],
        )
    assert result.exit_code == 0, result.stdout
    assert "2/2 deny checks passed" in result.stdout


def test_verify_rejects_seed_outside_seeds_dir(tmp_path: Path) -> None:
    """Identity name with `..` must not escape seeds-dir."""
    matrix = tmp_path / "matrix.json"
    _write_matrix(matrix, {"../escape": {"publish": ["lyra.foo"], "subscribe": []}})
    seeds = _seed_dir(tmp_path, [])
    result = runner.invoke(
        lyra_app,
        [
            "ops",
            "verify",
            "--matrix",
            str(matrix),
            "--seeds-dir",
            str(seeds),
        ],
    )
    assert result.exit_code != 0
    assert "outside seeds-dir" in result.output


def test_verify_acl_matrix_repo_loads() -> None:
    """The repo's real acl-matrix.json should load without error."""
    repo_root = Path(__file__).resolve().parents[2]
    matrix = repo_root / "deploy" / "nats" / "acl-matrix.json"
    if not matrix.exists():
        pytest.skip("acl-matrix.json not present")
    identities = _load_matrix(matrix)
    assert "hub" in identities
    assert isinstance(identities["hub"]["publish"], list)
