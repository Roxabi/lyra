"""Unit tests for lyra.nats.connect — nats_connect(), _read_nkey_seed(),
and _build_tls_context().

Mocks nats.connect so no real NATS server is required.
"""

from __future__ import annotations

import datetime
import ssl
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from roxabi_nats.connect import _build_tls_context, nats_connect


@pytest.fixture(scope="session")
def valid_ca_pem() -> str:
    """Generate a self-signed CA PEM once per test session."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "lyra-test-ca")]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


class TestNatsConnect:
    async def test_connect_with_seed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """nats.connect is called with nkeys_seed_str when seed file exists."""
        # Arrange
        seed_content = "SUAIBKIBKIB123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        seed_file = tmp_path / "nkey.seed"
        seed_file.write_text(seed_content)
        seed_file.chmod(0o600)
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", str(seed_file))

        mock_nc = AsyncMock()
        mock_conn = AsyncMock(return_value=mock_nc)
        with patch("roxabi_nats.connect.nats.connect", new=mock_conn) as mock_connect:
            # Act
            result = await nats_connect("nats://localhost:4222")

            # Assert
            mock_connect.assert_called_once()
            call_kwargs = mock_connect.call_args.kwargs
            assert call_kwargs["nkeys_seed_str"] == seed_content
            assert "error_cb" in call_kwargs
            assert "disconnected_cb" in call_kwargs
            assert "reconnected_cb" in call_kwargs
            assert result is mock_nc

    async def test_identity_name_sets_inbox_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """identity_name derives inbox_prefix=_INBOX.{name} (ADR-051)."""
        monkeypatch.delenv("NATS_NKEY_SEED_PATH", raising=False)

        mock_nc = AsyncMock()
        mock_conn = AsyncMock(return_value=mock_nc)
        with patch("roxabi_nats.connect.nats.connect", new=mock_conn) as mock_connect:
            await nats_connect("nats://localhost:4222", identity_name="clipool-worker")

            call_kwargs = mock_connect.call_args.kwargs
            assert call_kwargs["inbox_prefix"] == "_INBOX.clipool-worker"

    async def test_identity_name_does_not_override_explicit_inbox_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit inbox_prefix in **extra wins over identity_name."""
        monkeypatch.delenv("NATS_NKEY_SEED_PATH", raising=False)

        mock_nc = AsyncMock()
        mock_conn = AsyncMock(return_value=mock_nc)
        with patch("roxabi_nats.connect.nats.connect", new=mock_conn) as mock_connect:
            await nats_connect(
                "nats://localhost:4222",
                identity_name="hub",
                inbox_prefix="_INBOX.hub",
            )

            call_kwargs = mock_connect.call_args.kwargs
            assert call_kwargs["inbox_prefix"] == "_INBOX.hub"

    async def test_connect_without_seed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """nats.connect called without nkeys_seed_str when env var absent."""
        # Arrange
        monkeypatch.delenv("NATS_NKEY_SEED_PATH", raising=False)

        mock_nc = AsyncMock()
        mock_conn = AsyncMock(return_value=mock_nc)
        with patch("roxabi_nats.connect.nats.connect", new=mock_conn) as mock_connect:
            # Act
            result = await nats_connect("nats://localhost:4222")

            # Assert — nkeys_seed_str must NOT be present in the call
            mock_connect.assert_called_once()
            call_kwargs = mock_connect.call_args.kwargs
            assert "nkeys_seed_str" not in call_kwargs
            assert "error_cb" in call_kwargs
            assert result is mock_nc

    async def test_connect_missing_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SystemExit when NATS_NKEY_SEED_PATH points to missing file."""
        # Arrange
        missing = tmp_path / "does_not_exist.seed"
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", str(missing))

        # Act / Assert
        with pytest.raises(SystemExit, match="is not a file"):
            await nats_connect("nats://localhost:4222")

    async def test_connect_directory_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SystemExit when NATS_NKEY_SEED_PATH points to a directory."""
        # Arrange — tmp_path itself is a directory
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", str(tmp_path))

        # Act / Assert
        with pytest.raises(SystemExit, match="is not a file"):
            await nats_connect("nats://localhost:4222")

    async def test_connect_unreadable_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SystemExit when seed file exists but is unreadable."""
        # Arrange
        seed_file = tmp_path / "nkey.seed"
        seed_file.write_text("seed-content")
        seed_file.chmod(0o000)
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", str(seed_file))

        # Act / Assert
        with pytest.raises(SystemExit, match="unreadable"):
            await nats_connect("nats://localhost:4222")

    async def test_connect_empty_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SystemExit when seed file is empty or whitespace-only."""
        # Arrange
        seed_file = tmp_path / "nkey.seed"
        seed_file.write_text("   \n  ")
        seed_file.chmod(0o600)
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", str(seed_file))

        # Act / Assert
        with pytest.raises(SystemExit, match="is empty"):
            await nats_connect("nats://localhost:4222")

    async def test_connect_world_readable_seed_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SystemExit when seed file permissions are not 0o600."""
        # Arrange
        seed_file = tmp_path / "nkey.seed"
        seed_file.write_text("SU-valid-seed")
        seed_file.chmod(0o644)
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", str(seed_file))

        # Act / Assert
        with pytest.raises(SystemExit, match="unsafe permissions"):
            await nats_connect("nats://localhost:4222")


class TestBuildTlsContext:
    def test_returns_none_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """None when NATS_CA_CERT is absent (plain TCP / dev mode)."""
        monkeypatch.delenv("NATS_CA_CERT", raising=False)
        assert _build_tls_context() is None

    def test_returns_context_for_valid_pem(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        valid_ca_pem: str,
    ) -> None:
        """Returns an SSLContext when NATS_CA_CERT points to a valid PEM file."""
        ca_file = tmp_path / "ca.pem"
        ca_file.write_text(valid_ca_pem)
        monkeypatch.setenv("NATS_CA_CERT", str(ca_file))

        ctx = _build_tls_context()
        assert isinstance(ctx, ssl.SSLContext)

    def test_world_readable_ca_accepted(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        valid_ca_pem: str,
    ) -> None:
        """CA certs are not secret — 0o644 must be accepted (no unsafe-perm check)."""
        ca_file = tmp_path / "ca.pem"
        ca_file.write_text(valid_ca_pem)
        ca_file.chmod(0o644)
        monkeypatch.setenv("NATS_CA_CERT", str(ca_file))

        assert isinstance(_build_tls_context(), ssl.SSLContext)

    def test_missing_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SystemExit when NATS_CA_CERT points to a missing path."""
        monkeypatch.setenv("NATS_CA_CERT", str(tmp_path / "nope.pem"))
        with pytest.raises(SystemExit, match="is not a file"):
            _build_tls_context()

    def test_directory_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SystemExit when NATS_CA_CERT points to a directory."""
        monkeypatch.setenv("NATS_CA_CERT", str(tmp_path))
        with pytest.raises(SystemExit, match="is not a file"):
            _build_tls_context()

    def test_symlink_rejected(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        valid_ca_pem: str,
    ) -> None:
        """SystemExit when NATS_CA_CERT is a symlink (O_NOFOLLOW, ELOOP branch)."""
        target = tmp_path / "ca.pem"
        target.write_text(valid_ca_pem)
        link = tmp_path / "ca-link.pem"
        link.symlink_to(target)
        monkeypatch.setenv("NATS_CA_CERT", str(link))

        # O_NOFOLLOW raises OSError(ELOOP) on a non-dangling symlink →
        # mapped to "is not a file".
        with pytest.raises(SystemExit, match="is not a file"):
            _build_tls_context()

    def test_dangling_symlink_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SystemExit when NATS_CA_CERT is a symlink whose target is gone
        (O_NOFOLLOW guard, ENOENT branch)."""
        # Target content is irrelevant — os.open never reads it because
        # ENOENT fires on symlink resolution.
        target = tmp_path / "ca.pem"
        target.write_text("placeholder")
        link = tmp_path / "ca-link.pem"
        link.symlink_to(target)
        target.unlink()  # leave the symlink dangling
        monkeypatch.setenv("NATS_CA_CERT", str(link))

        with pytest.raises(SystemExit, match="is not a file"):
            _build_tls_context()

    def test_empty_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """SystemExit when NATS_CA_CERT points to an empty / whitespace file."""
        ca_file = tmp_path / "ca.pem"
        ca_file.write_text("   \n  ")
        monkeypatch.setenv("NATS_CA_CERT", str(ca_file))

        with pytest.raises(SystemExit, match="is empty"):
            _build_tls_context()

    def test_invalid_pem(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """SystemExit when NATS_CA_CERT is not valid PEM."""
        ca_file = tmp_path / "ca.pem"
        ca_file.write_text("not a real certificate")
        monkeypatch.setenv("NATS_CA_CERT", str(ca_file))

        with pytest.raises(SystemExit, match="not a valid PEM bundle"):
            _build_tls_context()

    def test_unreadable_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SystemExit when NATS_CA_CERT is mode 0o000."""
        ca_file = tmp_path / "ca.pem"
        ca_file.write_text("irrelevant")
        ca_file.chmod(0o000)
        monkeypatch.setenv("NATS_CA_CERT", str(ca_file))

        try:
            with pytest.raises(SystemExit, match="unreadable"):
                _build_tls_context()
        finally:
            ca_file.chmod(0o600)  # let tmp_path cleanup succeed
