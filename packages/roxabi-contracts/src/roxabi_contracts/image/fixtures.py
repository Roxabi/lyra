"""Test fixtures for roxabi_contracts.image.

Hard-coded minimal PNG bytes. No runtime dependencies — the fixture is a
pre-built 1x1 transparent PNG so the [testing] extra does not need an
image library for this module. ``scipy`` / ``numpy`` remain listed under
the voice extra; image fixtures need neither.
"""

from __future__ import annotations

# 1x1 transparent PNG — 67 bytes. Produced once offline; pinned here so
# the fixture is deterministic and byte-identical across environments.
# Header: 89 50 4E 47 (PNG signature). Width/height 1 (big-endian), 8-bit
# RGBA. Single IDAT chunk with a zero pixel, IEND terminator.
tiny_png_1x1: bytes = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
"""1x1 transparent PNG, 67 bytes. Header starts with ``b'\\x89PNG'``."""

tiny_png_mime: str = "image/png"
"""MIME type paired with ``tiny_png_1x1`` for ImageResponse fixtures."""

tiny_png_width: int = 1
"""Pixel width of ``tiny_png_1x1``."""

tiny_png_height: int = 1
"""Pixel height of ``tiny_png_1x1``."""
