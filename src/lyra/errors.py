"""Provider-agnostic exceptions for LLM drivers."""

from __future__ import annotations


class ProviderError(Exception):
    """Provider-agnostic wrapper for LLM SDK errors.

    Raised by LLM drivers so that core code can react to provider failures
    without importing any SDK-specific exception.
    """
