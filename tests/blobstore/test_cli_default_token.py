"""Non-regression: blobstore CLI default token path uses factory_blobstore_token."""

from __future__ import annotations

from factory.blobstore import cli


def test_default_token_path_is_factory() -> None:
    assert str(cli._DEFAULT_TOKEN_PATH) == "/run/secrets/factory_blobstore_token"
    assert "lyra" not in str(cli._DEFAULT_TOKEN_PATH)
