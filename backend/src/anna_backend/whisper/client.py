from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from anna_backend.media_types import WordTiming


class WhisperResponseError(ValueError):
    """The transcription response does not satisfy the word-timestamp contract."""


def _value(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def parse_word_timestamps(payload: Any) -> tuple[WordTiming, ...]:
    raw_words = _value(payload, "words")
    if not isinstance(raw_words, Sequence) or isinstance(raw_words, (str, bytes)):
        raise WhisperResponseError("Whisper response must contain a words array")

    words: list[WordTiming] = []
    previous_end = 0.0
    for index, item in enumerate(raw_words):
        word = str(_value(item, "word") or "").strip()
        try:
            start = float(_value(item, "start"))
            end = float(_value(item, "end"))
        except (TypeError, ValueError) as error:
            raise WhisperResponseError(f"Invalid timestamps for word {index}") from error

        if not word:
            raise WhisperResponseError(f"Whisper word {index} is empty")
        if start < 0 or end < start:
            raise WhisperResponseError(f"Invalid interval for Whisper word {index}: {start}..{end}")
        if end == start:
            end = start + 0.01
        if start + 0.05 < previous_end:
            raise WhisperResponseError(f"Whisper words are not monotonic at index {index}")

        words.append(WordTiming(word=word, start_seconds=start, end_seconds=end))
        previous_end = max(previous_end, end)

    if not words:
        raise WhisperResponseError("Whisper response contains no words")
    return tuple(words)


class OpenAIWhisperWordTranscriber:
    """Production word-timestamp adapter for the OpenAI Transcriptions API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "whisper-1",
        language: str = "de",
        client: AsyncOpenAI | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key must not be empty")
        self._client = client or AsyncOpenAI(api_key=api_key, max_retries=0)
        self._model = model
        self._language = language

    @staticmethod
    def _strategy_temperature(strategy: str) -> float:
        strategy_temperatures = {"word": 0.0, "sentence": 0.2}
        if strategy not in strategy_temperatures:
            raise ValueError(f"Unsupported Whisper strategy: {strategy}")
        return strategy_temperatures[strategy]

    async def transcribe(
        self, audio_path: Path, *, strategy: str = "word"
    ) -> tuple[WordTiming, ...]:
        temperature = self._strategy_temperature(strategy)
        if not audio_path.is_file() or audio_path.stat().st_size <= 0:
            raise FileNotFoundError(f"Audio is missing or empty: {audio_path}")

        with audio_path.open("rb") as audio_file:
            response = await self._client.audio.transcriptions.create(
                file=audio_file,
                model=self._model,
                language=self._language,
                response_format="verbose_json",
                timestamp_granularities=["word"],
                temperature=temperature,
            )
        return parse_word_timestamps(response)
