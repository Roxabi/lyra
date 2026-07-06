"""Tests for _seeds_dir() and root-guard changes in scripts/_modes.py — #1718 T15.

Covers:
  - _seeds_dir() honors ROXABI_FACTORY_DIR (path under it/nkeys)
  - _seeds_dir() honors SEEDS_DIR override (wins over ROXABI_FACTORY_DIR)
  - Default path does not contain '.lyra' (resolved to factory_data_dir()/nkeys)
  - Root guard relaxed: rootless seed generation does NOT call _require_root()
    when FACTORY_ACL_WRITE_ETC_NATS is unset
  - Opt-in path: FACTORY_ACL_WRITE_ETC_NATS=1 gates the /etc/nats write behind
    _require_root(); AUTH_DIR override bypasses actual root requirement
"""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import scripts._modes as _modes
from scripts._modes import (
    _etc_nats_write_enabled,
    _mode_full_provision,
    _require_root,
    _seeds_dir,
)

from tests.fakes.nkey_provider import FakeNkeyProvider

# ── _seeds_dir() unit tests ───────────────────────────────────────────────────


class TestSeedsDirResolution:
    """_seeds_dir() resolves correctly under various env configurations."""

    def test_honors_roxabi_factory_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ROXABI_FACTORY_DIR set → _seeds_dir() returns <ROXABI_FACTORY_DIR>/nkeys.

        Verified: deleting the ROXABI_FACTORY_DIR branch in _seeds_dir() would
        cause this test to fail (path would be ~/.roxabi/factory/nkeys, not tmp_path).
        """
        # Arrange
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        monkeypatch.delenv("SEEDS_DIR", raising=False)

        # Act
        result = _seeds_dir()

        # Assert
        assert result == tmp_path / "nkeys", (
            f"Expected {tmp_path / 'nkeys'}; got {result}"
        )

    def test_seeds_dir_override_wins_over_roxabi_factory_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SEEDS_DIR env overrides ROXABI_FACTORY_DIR: SEEDS_DIR always wins.

        Verified: removing the `if env:` branch in _seeds_dir() would cause this
        test to fail (SEEDS_DIR would be ignored).
        """
        # Arrange
        factory_dir = tmp_path / "factory"
        seeds_override = tmp_path / "custom_seeds"
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(factory_dir))
        monkeypatch.setenv("SEEDS_DIR", str(seeds_override))

        # Act
        result = _seeds_dir()

        # Assert — SEEDS_DIR wins unconditionally
        assert result == seeds_override, (
            f"Expected SEEDS_DIR override {seeds_override}; got {result}"
        )

    def test_default_does_not_contain_lyra(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default path must not contain '.lyra' (old naming).

        The canonical default is factory_data_dir()/nkeys which resolves to
        ~/.roxabi/factory/nkeys, NOT ~/.lyra/nkeys.

        Verified: if _seeds_dir() were changed back to Path.home() / '.lyra' / 'nkeys'
        this assertion would catch it.
        """
        # Arrange — clear both overrides so the code falls through to the default
        monkeypatch.delenv("SEEDS_DIR", raising=False)
        monkeypatch.delenv("ROXABI_FACTORY_DIR", raising=False)

        # Act
        result = _seeds_dir()

        # Assert
        assert ".lyra" not in str(result), (
            f"Default _seeds_dir() must not contain '.lyra'; got {result}"
        )

    def test_default_is_factory_data_dir_nkeys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default: factory_data_dir()/nkeys == <ROXABI_FACTORY_DIR>/nkeys.

        Both sides read from factory_data_dir(), so setting ROXABI_FACTORY_DIR
        gives a deterministic assertion without touching the real home dir.
        """
        # Arrange
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        monkeypatch.delenv("SEEDS_DIR", raising=False)

        from factory.paths import factory_data_dir

        expected = factory_data_dir() / "nkeys"

        # Act
        result = _seeds_dir()

        # Assert
        assert result == expected, (
            f"_seeds_dir() default must equal factory_data_dir()/nkeys; "
            f"expected {expected}, got {result}"
        )


# ── Root guard / opt-in path tests ───────────────────────────────────────────


class TestRootGuardRelaxed:
    """Rootless seed generation must NOT call _require_root() when opt-in is off."""

    def test_etc_nats_write_disabled_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FACTORY_ACL_WRITE_ETC_NATS unset → _etc_nats_write_enabled() is False.

        Verified: changing the default in _etc_nats_write_enabled() to return True
        would break this.
        """
        # Arrange
        monkeypatch.delenv("FACTORY_ACL_WRITE_ETC_NATS", raising=False)

        # Act / Assert
        assert _etc_nats_write_enabled() is False

    def test_etc_nats_write_enabled_when_set_to_1(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FACTORY_ACL_WRITE_ETC_NATS=1 → _etc_nats_write_enabled() is True."""
        monkeypatch.setenv("FACTORY_ACL_WRITE_ETC_NATS", "1")
        assert _etc_nats_write_enabled() is True

    def test_etc_nats_write_disabled_for_other_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only '1' enables the opt-in; other values (e.g., 'true', '0') do not."""
        for val in ("0", "true", "yes", ""):
            monkeypatch.setenv("FACTORY_ACL_WRITE_ETC_NATS", val)
            assert _etc_nats_write_enabled() is False, (
                f"Expected False for FACTORY_ACL_WRITE_ETC_NATS={val!r}"
            )

    def test_full_provision_rootless_does_not_call_require_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_mode_full_provision does NOT call _require_root() when opt-in is off.

        This is the key root-guard-relaxed test: deleting the `if write_etc:
        _require_root()` guard from _mode_full_provision would not matter here,
        but a regression re-adding an unconditional _require_root() call WOULD
        be caught (require_root mock raises AssertionError to make it visible).

        Verified: if _mode_full_provision called _require_root() unconditionally,
        the mock's side_effect would fire and the test would fail.
        """
        # Arrange — no opt-in, rootless path
        monkeypatch.delenv("FACTORY_ACL_WRITE_ETC_NATS", raising=False)
        seeds_dir = tmp_path / "nkeys"
        auth_dir = tmp_path / "auth"
        auth_dir.mkdir(parents=True)
        monkeypatch.setenv("SEEDS_DIR", str(seeds_dir))
        monkeypatch.setenv("AUTH_DIR", str(auth_dir))
        # Hermeticity: _mode_full_provision now calls rotation_log_append() per
        # active identity (#2246) — without these overrides this test would
        # write a real "secret:hub" line to the developer's actual
        # ~/.roxabi/factory/rotation-log.md.
        monkeypatch.setenv("ROTATION_LOG", str(tmp_path / "rotation-log.md"))
        monkeypatch.setenv("OPERATOR_LOG", str(tmp_path / "operator.log"))
        monkeypatch.setattr(_modes, "_provider_factory", FakeNkeyProvider)

        # Minimal 1-identity matrix
        import json

        matrix_path = tmp_path / "matrix.json"
        matrix_path.write_text(
            json.dumps(
                {
                    "version": "2",
                    "identities": {
                        "hub": {
                            "status": "active",
                            "created_at": "2026-01-01",
                            "owner": "factory",
                            "description": "hub",
                            "allow_responses": False,
                            "publish": ["factory.out.>"],
                            "subscribe": ["factory.in.>"],
                            "deploy": {"type": "container", "secret": "s"},
                        }
                    },
                }
            )
        )
        args = argparse.Namespace(
            matrix=matrix_path, yes=True, ack_external_distribution=True
        )

        require_root_mock = MagicMock(
            side_effect=AssertionError("_require_root must NOT be called rootless path")
        )

        # Act — patch _require_root; if called, the mock raises
        with patch("scripts._modes._require_root", require_root_mock):
            # Should complete without raising
            result = _mode_full_provision(args)

        # Assert — provision succeeded, _require_root was never called
        require_root_mock.assert_not_called()
        assert isinstance(result, list)

    def test_full_provision_opt_in_calls_require_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With FACTORY_ACL_WRITE_ETC_NATS=1, _mode_full_provision calls _require_root.

        AUTH_DIR override is set so _require_root returns early (no real root needed).
        The test verifies that the guard is wired: a mock spy confirms the call.

        Verified: removing the `if write_etc: _require_root()` branch from
        _mode_full_provision would cause require_root_mock.assert_called_once()
        to fail.
        """
        # Arrange — enable opt-in
        monkeypatch.setenv("FACTORY_ACL_WRITE_ETC_NATS", "1")
        seeds_dir = tmp_path / "nkeys"
        auth_dir = tmp_path / "auth"
        auth_dir.mkdir(parents=True)
        monkeypatch.setenv("SEEDS_DIR", str(seeds_dir))
        monkeypatch.setenv("AUTH_DIR", str(auth_dir))  # bypasses real root check
        # Hermeticity: _mode_full_provision now calls rotation_log_append() per
        # active identity (#2246) — without these overrides this test would
        # write a real "secret:hub" line to the developer's actual
        # ~/.roxabi/factory/rotation-log.md.
        monkeypatch.setenv("ROTATION_LOG", str(tmp_path / "rotation-log.md"))
        monkeypatch.setenv("OPERATOR_LOG", str(tmp_path / "operator.log"))
        monkeypatch.setattr(_modes, "_provider_factory", FakeNkeyProvider)

        import json

        matrix_path = tmp_path / "matrix.json"
        matrix_path.write_text(
            json.dumps(
                {
                    "version": "2",
                    "identities": {
                        "hub": {
                            "status": "active",
                            "created_at": "2026-01-01",
                            "owner": "factory",
                            "description": "hub",
                            "allow_responses": False,
                            "publish": ["factory.out.>"],
                            "subscribe": ["factory.in.>"],
                            "deploy": {"type": "container", "secret": "s"},
                        }
                    },
                }
            )
        )
        args = argparse.Namespace(
            matrix=matrix_path, yes=True, ack_external_distribution=True
        )

        # Spy on the real _require_root (AUTH_DIR set → it returns immediately)
        with patch("scripts._modes._require_root", wraps=_require_root) as spy:
            _mode_full_provision(args)

        # Assert — _require_root was called once (the opt-in gate is wired)
        spy.assert_called_once()

    def test_require_root_returns_early_with_auth_dir_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_require_root() returns early when AUTH_DIR is set (test override path).

        Verified: removing the `if os.environ.get('AUTH_DIR'): return` guard from
        _require_root would cause it to call os.geteuid() and potentially sys.exit(1)
        in non-root CI.
        """
        # Arrange — set AUTH_DIR to redirect; set FACTORY_ACL_WRITE_ETC_NATS=1
        # so we'd normally need root
        monkeypatch.setenv("AUTH_DIR", str(tmp_path))
        monkeypatch.setenv("FACTORY_ACL_WRITE_ETC_NATS", "1")

        # Act — should NOT raise SystemExit or call sys.exit
        # If the guard were absent, non-root CI would hit sys.exit(1)
        _require_root()  # must not raise
