from __future__ import annotations

import os
from pathlib import Path
from tempfile import mkstemp
from typing import Any

from openai import AsyncOpenAI

from anna_backend.tts.profile import ANNA_MARIN_PROFILE, SpeechProfile


class TTSGenerationError(RuntimeError):
    """The provider did not produce a valid audio artifact."""


class OpenAIAudioGenerator:
    """Single-call OpenAI TTS adapter with atomic destination replacement."""

    def __init__(
        self,
        *,
        api_key: str,
        profile: SpeechProfile = ANNA_MARIN_PROFILE,
        client: AsyncOpenAI | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key must not be empty")
        profile.validate()
        self._client = client or AsyncOpenAI(api_key=api_key, max_retries=0)
        self._profile = profile

    async def synthesize(self, spoken_text: str, destination: Path) -> Path:
        if not spoken_text.strip():
            raise ValueError("spoken_text must not be empty")
        if "\n" in spoken_text or "\r" in spoken_text:
            raise ValueError("spoken_text must be the canonical one-line TTS input")
        expected_suffix = f".{self._profile.response_format}"
        if destination.suffix.lower() != expected_suffix:
            raise ValueError(f"TTS destination must use the {expected_suffix} extension")

        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            response: Any = await self._client.audio.speech.create(
                model=self._profile.model,
                voice=self._profile.voice,
                input=spoken_text,
                instructions=self._profile.instructions,
                response_format=self._profile.response_format,
                speed=self._profile.speed,
            )
            payload = getattr(response, "content", None)
            if not isinstance(payload, (bytes, bytearray)):
                read = getattr(response, "aread", None)
                payload = await read() if callable(read) else None
            if not isinstance(payload, (bytes, bytearray)) or not payload:
                raise TTSGenerationError("OpenAI TTS returned empty audio")

            with temporary.open("wb") as audio_file:
                audio_file.write(payload)
                audio_file.flush()
                os.fsync(audio_file.fileno())
            os.replace(temporary, destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        return destination
