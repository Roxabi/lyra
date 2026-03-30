"""FfmpegConverter — AudioConverter backed by ffmpeg (issue #362).

Converts WAV → OGG/Opus (48 kHz, mono) using ffmpeg via async subprocess.
"""

from __future__ import annotations

import asyncio
import logging
from asyncio.subprocess import PIPE
from pathlib import Path

from lyra.integrations.base import AudioConversionFailed

log = logging.getLogger(__name__)


class FfmpegConverter:
    """AudioConverter backed by ffmpeg."""

    async def convert_wav_to_ogg(self, wav_path: Path, ogg_path: Path) -> None:
        """Convert WAV to OGG/Opus (48 kHz, mono).

        Raises:
            AudioConversionFailed("not_available")    — ffmpeg not on PATH.
            AudioConversionFailed("subprocess_error") — non-zero exit.
            AudioConversionFailed("timeout")          — exceeded 30s timeout.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-i",
                str(wav_path),
                "-c:a",
                "libopus",
                "-ar",
                "48000",
                "-ac",
                "1",
                "-y",
                str(ogg_path),
                stdout=PIPE,
                stderr=PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise AudioConversionFailed("timeout")
            if proc.returncode != 0:
                log.warning(
                    "FfmpegConverter: exited %d: %s",
                    proc.returncode,
                    stderr.decode()[:200],
                )
                raise AudioConversionFailed("subprocess_error")
        except FileNotFoundError:
            raise AudioConversionFailed("not_available")
