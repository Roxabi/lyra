"""RED-phase contract tests for roxabi_contracts.BlobNotFoundError.

Tests will fail with ImportError until backend-dev implements
BlobNotFoundError in packages/roxabi-contracts/src/roxabi_contracts/errors.py
and re-exports it from the top-level roxabi_contracts package.
"""

from __future__ import annotations


def test_blob_not_found_error_top_level_reexport_matches_errors_module() -> None:
    """Top-level re-export and errors-module path resolve to the same class.

    Negative guard: if the re-export shim is removed from roxabi_contracts.__init__
    or errors.py, this test fails with ImportError or with `A is not B`.
    """
    # Arrange / Act
    from roxabi_contracts import BlobNotFoundError as A
    from roxabi_contracts.errors import BlobNotFoundError as B

    # Assert — both paths resolve to the identical class object
    assert A is B


def test_blob_not_found_error_is_exception_subclass() -> None:
    """BlobNotFoundError must be a subclass of Exception."""
    from roxabi_contracts import BlobNotFoundError as A

    assert issubclass(A, Exception)


def test_blob_not_found_error_carries_key_in_str() -> None:
    """A single positional argument (the blob key) appears in str(error).

    Negative guard: if BlobNotFoundError is defined as a bare
    `class BlobNotFoundError(Exception): pass` with no __init__ that stores
    the key, str() would be empty and this test fails.
    """
    from roxabi_contracts import BlobNotFoundError as A

    err = A("k")
    assert "k" in str(err)
