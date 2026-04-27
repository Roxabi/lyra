"""Domain ports — pure Protocol definitions with no infrastructure imports."""

from lyra.core.ports.llm import LlmProvider, LlmResult
from lyra.core.ports.stt import STTProtocol
from lyra.core.ports.tts import TtsProtocol

__all__ = [
    "LlmProvider",
    "LlmResult",
    "STTProtocol",
    "TtsProtocol",
]
