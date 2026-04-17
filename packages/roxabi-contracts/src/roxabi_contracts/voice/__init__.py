"""Voice-domain NATS contract surface.

V1 minimal: re-exports the subject namespace only. The four envelope
models land in the next slice and extend ``__all__`` accordingly. The
``fixtures`` submodule is test-only and DELIBERATELY NOT re-exported.
"""

from roxabi_contracts.voice.subjects import SUBJECTS

__all__ = ["SUBJECTS"]
