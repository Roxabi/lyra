"""Contract tests for roxabi_contracts.BlobNotFoundError.

BlobNotFoundError is defined in
packages/roxabi-contracts/src/roxabi_contracts/blob_errors.py
and re-exported from the top-level roxabi_contracts package.
"""

from __future__ import annotations


def test_blob_not_found_error_top_level_reexport_matches_blob_errors_module() -> None:
    """Top-level re-export and blob_errors-module path resolve to the same class.

    Negative guard: if the re-export shim is removed from roxabi_contracts.__init__
    or blob_errors.py, this test fails with ImportError or with `A is not B`.
    """
    # Arrange / Act
    from roxabi_contracts import BlobNotFoundError as A
    from roxabi_contracts.blob_errors import BlobNotFoundError as B

    # Assert — both paths resolve to the identical class object
    assert A is B


def test_blob_not_found_error_is_exception_subclass() -> None:
    """BlobNotFoundError must be a subclass of Exception."""
    from roxabi_contracts import BlobNotFoundError as A

    assert issubclass(A, Exception)


def test_blob_not_found_error_carries_key_attribute() -> None:
    """A single positional argument (the blob key) is stored on .key.

    Negative guard: if BlobNotFoundError is defined as a bare
    `class BlobNotFoundError(Exception): pass` with no __init__ that stores
    the key, err.key would raise AttributeError and this test fails.
    """
    from roxabi_contracts import BlobNotFoundError as A

    err = A("k")
    assert err.key == "k"


def test_blob_not_found_error_redacts_long_key_in_str() -> None:
    """str(err) must NOT expose a long key wholesale; .key stays raw."""
    from roxabi_contracts import BlobNotFoundError as A

    long_key = "x" * 40
    err = A(long_key)

    # .key is always the raw value
    assert err.key == long_key

    # str(err) must not contain the full key
    assert long_key not in str(err)

    # str(err) must contain the length hint
    assert "40 chars" in str(err)
