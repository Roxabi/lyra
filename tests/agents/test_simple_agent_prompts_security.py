"""Security tests for audio attachment path validation — REMOVED (#1553).

The ``_validate_audio_path`` and ``_safe_read_bytes`` helpers were deleted
in #1553 when the dead ``type="audio"`` attachment STT path was removed.
Voice STT is handled exclusively by SttMiddleware (msg.audio.blob_ref path).

This file is intentionally empty — kept as a tombstone so the git history
explains the removal.
"""
