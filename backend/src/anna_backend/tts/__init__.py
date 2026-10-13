"""TTS adapter with immutable spokenText and bounded retries."""

from anna_backend.tts.client import (
    OpenAIAudioGenerator,
    TTSGenerationError,
)
from anna_backend.tts.profile import (
    ANNA_MARIN_INSTRUCTIONS,
    ANNA_MARIN_PROFILE,
    SpeechProfile,
)

__all__ = [
    "ANNA_MARIN_INSTRUCTIONS",
    "ANNA_MARIN_PROFILE",
    "OpenAIAudioGenerator",
    "SpeechProfile",
    "TTSGenerationError",
]
