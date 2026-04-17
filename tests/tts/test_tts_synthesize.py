"""TTSService.synthesize() — core behaviour, cleanup, language/voice."""

from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("voicecli") is None,
    reason="voicecli not installed (optional voice extra)",
)

from lyra.tts import (  # noqa: E402
    SynthesisResult,
    TTSConfig,
    TTSService,
)

from .conftest import make_chunked_result, write_minimal_wav  # noqa: E402


def _make_ogg_converter(ogg_bytes: bytes = b"fakeoggdata") -> AsyncMock:
    """Return a mock AudioConverter that writes ogg_bytes to out_path."""

    async def _fake_convert(wav_path: Path, out_path: Path) -> None:
        out_path.write_bytes(ogg_bytes)

    converter = AsyncMock()
    converter.convert_wav_to_ogg = AsyncMock(side_effect=_fake_convert)
    return converter


# ---------------------------------------------------------------------------
# TTSService.synthesize() — delegates to voiceCLI
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_returns_synthesis_result():
    """synthesize() returns SynthesisResult with OGG audio_bytes and audio/ogg mime."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name

    expected_bytes = b"fakeoggdata"
    converter = _make_ogg_converter(expected_bytes)
    svc = TTSService(TTSConfig(), converter=converter)

    write_minimal_wav(wav_path, duration_ms=500)

    async def fake_generate(text, **kwargs):
        write_minimal_wav(wav_path, duration_ms=500)
        return make_chunked_result([wav_path])

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_generate)):
        result = await svc.synthesize("Hello world")

    assert isinstance(result, SynthesisResult)
    assert result.audio_bytes == expected_bytes
    assert result.mime_type == "audio/ogg"
    assert result.duration_ms is not None  # computed from WAV before OGG conversion
    assert result.waveform_b64 is not None
    converter.convert_wav_to_ogg.assert_awaited_once()


@pytest.mark.asyncio
async def test_synthesize_cleans_up_temp_file_on_success():
    """synthesize() deletes temp WAV chunk and OGG files after success."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name

    converter = _make_ogg_converter()
    svc = TTSService(TTSConfig(), converter=converter)

    write_minimal_wav(wav_path, duration_ms=200)

    async def fake_generate(text, **kwargs):
        write_minimal_wav(wav_path, duration_ms=200)
        return make_chunked_result([wav_path])

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_generate)):
        await svc.synthesize("Cleanup test")

    ogg_path = Path(wav_path).with_suffix(".ogg")
    assert not os.path.exists(wav_path), "WAV file must be deleted after synthesis"
    assert not ogg_path.exists(), "OGG file must be deleted after synthesis"


@pytest.mark.asyncio
async def test_synthesize_cleans_up_temp_file_on_failure():
    """synthesize() deletes the temp file even when generate() raises."""
    svc = TTSService(TTSConfig())

    captured_path: list[str] = []
    real_mkstemp = tempfile.mkstemp

    def spy_mkstemp(**kwargs):
        fd, path = real_mkstemp(**kwargs)
        captured_path.append(path)
        return fd, path

    async def fake_generate_error(text, **kwargs):
        raise RuntimeError("TTS backend failed")

    mock_gen = AsyncMock(side_effect=fake_generate_error)
    with patch("tempfile.mkstemp", side_effect=spy_mkstemp):
        with patch("voicecli.generate_async", new=mock_gen):
            with pytest.raises(RuntimeError, match="TTS backend failed"):
                await svc.synthesize("Failure cleanup test")

    assert captured_path, "mkstemp must have been called"
    assert not os.path.exists(captured_path[0]), "temp file must be deleted on failure"


# ---------------------------------------------------------------------------
# S1 — T02: synthesize() language/voice params
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_passes_language_to_generate():
    """synthesize(language=...) forwards language kwarg to generate_async."""
    captured: dict = {}

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name

    converter = _make_ogg_converter()
    svc = TTSService(TTSConfig(), converter=converter)

    write_minimal_wav(wav_path)

    async def fake_gen(text, **kwargs):
        captured.update(kwargs)
        write_minimal_wav(wav_path)
        return make_chunked_result([wav_path])

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
        await svc.synthesize("Hello", language="fr")

    assert captured.get("language") == "french"  # ISO code normalized to full name


@pytest.mark.asyncio
async def test_synthesize_language_none_uses_init_value():
    """synthesize() with language=None uses the TTSConfig.language from init."""
    captured: dict = {}

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name

    converter = _make_ogg_converter()
    svc = TTSService(TTSConfig(language="English"), converter=converter)

    write_minimal_wav(wav_path)

    async def fake_gen(text, **kwargs):
        captured.update(kwargs)
        write_minimal_wav(wav_path)
        return make_chunked_result([wav_path])

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
        await svc.synthesize("Hello")  # no language override

    assert captured.get("language") == "English"


# ---------------------------------------------------------------------------
# Text normalization for TTS (#627)
# ---------------------------------------------------------------------------


class TestTextNormalization:
    """Tests for _normalize_text_for_tts — regression tests for #627."""

    @pytest.mark.asyncio
    async def test_multiline_text_normalized_to_single_line(self) -> None:
        """Multiline text → newlines replaced with spaces."""
        captured_text: list[str] = []

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = tmp.name

        converter = _make_ogg_converter()
        svc = TTSService(TTSConfig(), converter=converter)

        write_minimal_wav(wav_path)

        async def fake_gen(text, **kwargs):
            captured_text.append(text)
            write_minimal_wav(wav_path)
            return make_chunked_result([wav_path])

        with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
            await svc.synthesize("Line one\nLine two\nLine three")

        assert len(captured_text) == 1
        assert "\n" not in captured_text[0]
        assert captured_text[0] == "Line one Line two Line three"

    @pytest.mark.asyncio
    async def test_markdown_bold_stripped(self) -> None:
        """Markdown bold (**text**) → stripped to plain text."""
        captured_text: list[str] = []

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = tmp.name

        converter = _make_ogg_converter()
        svc = TTSService(TTSConfig(), converter=converter)

        write_minimal_wav(wav_path)

        async def fake_gen(text, **kwargs):
            captured_text.append(text)
            write_minimal_wav(wav_path)
            return make_chunked_result([wav_path])

        with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
            await svc.synthesize("This is **bold** text")

        assert captured_text[0] == "This is bold text"

    @pytest.mark.asyncio
    async def test_markdown_italic_stripped(self) -> None:
        """Markdown italic (*text*) → stripped to plain text."""
        captured_text: list[str] = []

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = tmp.name

        converter = _make_ogg_converter()
        svc = TTSService(TTSConfig(), converter=converter)

        write_minimal_wav(wav_path)

        async def fake_gen(text, **kwargs):
            captured_text.append(text)
            write_minimal_wav(wav_path)
            return make_chunked_result([wav_path])

        with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
            await svc.synthesize("This is *italic* text")

        assert captured_text[0] == "This is italic text"

    @pytest.mark.asyncio
    async def test_markdown_code_stripped(self) -> None:
        """Markdown code (`text`) → stripped to plain text."""
        captured_text: list[str] = []

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = tmp.name

        converter = _make_ogg_converter()
        svc = TTSService(TTSConfig(), converter=converter)

        write_minimal_wav(wav_path)

        async def fake_gen(text, **kwargs):
            captured_text.append(text)
            write_minimal_wav(wav_path)
            return make_chunked_result([wav_path])

        with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
            await svc.synthesize("Run `npm install` to install")

        assert captured_text[0] == "Run npm install to install"

    @pytest.mark.asyncio
    async def test_markdown_heading_stripped(self) -> None:
        """Markdown heading (# text) → stripped."""
        captured_text: list[str] = []

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = tmp.name

        converter = _make_ogg_converter()
        svc = TTSService(TTSConfig(), converter=converter)

        write_minimal_wav(wav_path)

        async def fake_gen(text, **kwargs):
            captured_text.append(text)
            write_minimal_wav(wav_path)
            return make_chunked_result([wav_path])

        with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
            await svc.synthesize("## Introduction\nThis is the intro.")

        assert captured_text[0] == "Introduction This is the intro."

    @pytest.mark.asyncio
    async def test_url_replaced_with_link(self) -> None:
        """URLs → replaced with 'link' placeholder."""
        captured_text: list[str] = []

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = tmp.name

        converter = _make_ogg_converter()
        svc = TTSService(TTSConfig(), converter=converter)

        write_minimal_wav(wav_path)

        async def fake_gen(text, **kwargs):
            captured_text.append(text)
            write_minimal_wav(wav_path)
            return make_chunked_result([wav_path])

        with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
            await svc.synthesize("Check https://example.com for more info")

        assert captured_text[0] == "Check link for more info"

    @pytest.mark.asyncio
    async def test_markdown_link_text_preserved(self) -> None:
        """Markdown links [text](url) → text preserved, url stripped."""
        captured_text: list[str] = []

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = tmp.name

        converter = _make_ogg_converter()
        svc = TTSService(TTSConfig(), converter=converter)

        write_minimal_wav(wav_path)

        async def fake_gen(text, **kwargs):
            captured_text.append(text)
            write_minimal_wav(wav_path)
            return make_chunked_result([wav_path])

        with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
            await svc.synthesize(
                "Read the [documentation](https://example.com) for details"
            )

        assert captured_text[0] == "Read the documentation for details"

    @pytest.mark.asyncio
    async def test_multiple_spaces_collapsed(self) -> None:
        """Multiple spaces → collapsed to single space."""
        captured_text: list[str] = []

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = tmp.name

        converter = _make_ogg_converter()
        svc = TTSService(TTSConfig(), converter=converter)

        write_minimal_wav(wav_path)

        async def fake_gen(text, **kwargs):
            captured_text.append(text)
            write_minimal_wav(wav_path)
            return make_chunked_result([wav_path])

        with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
            await svc.synthesize("Too    many     spaces")

        assert captured_text[0] == "Too many spaces"

    @pytest.mark.asyncio
    async def test_complex_markdown_normalized(self) -> None:
        """Complex markdown with multiple features → clean text."""
        captured_text: list[str] = []

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = tmp.name

        converter = _make_ogg_converter()
        svc = TTSService(TTSConfig(), converter=converter)

        write_minimal_wav(wav_path)

        async def fake_gen(text, **kwargs):
            captured_text.append(text)
            write_minimal_wav(wav_path)
            return make_chunked_result([wav_path])

        complex_input = """## Summary

This is **bold** and *italic* text.
Check `code` and [links](https://example.com).
Visit https://example.org for more.

Multiple   spaces   here."""

        with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
            await svc.synthesize(complex_input)

        # Should be clean: no newlines, no markdown, collapsed spaces
        result = captured_text[0]
        assert "\n" not in result
        assert "**" not in result
        assert "*" not in result
        assert "`" not in result
        assert "#" not in result
        assert "https://" not in result
        assert "  " not in result  # no double spaces
        assert result.startswith("Summary")
