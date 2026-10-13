from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from anna_backend.tts import OpenAIAudioGenerator, TTSGenerationError
from anna_backend.whisper import OpenAIWhisperWordTranscriber


class _SpeechEndpoint:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    async def create(self, **payload: Any) -> SimpleNamespace:
        self.calls.append(payload)
        return SimpleNamespace(content=self.payload)


def _client(endpoint: _SpeechEndpoint) -> Any:
    return SimpleNamespace(audio=SimpleNamespace(speech=endpoint))


def test_openai_tts_client_disables_sdk_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def build_client(**values: Any) -> object:
        calls.append(values)
        return object()

    monkeypatch.setattr("anna_backend.tts.client.AsyncOpenAI", build_client)

    OpenAIAudioGenerator(api_key="test-key")

    assert calls == [{"api_key": "test-key", "max_retries": 0}]


def test_openai_whisper_client_disables_sdk_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def build_client(**values: Any) -> object:
        calls.append(values)
        return object()

    monkeypatch.setattr("anna_backend.whisper.client.AsyncOpenAI", build_client)

    OpenAIWhisperWordTranscriber(api_key="test-key")

    assert calls == [{"api_key": "test-key", "max_retries": 0}]


@pytest.mark.asyncio
async def test_openai_tts_uses_exact_spoken_text_and_anna_profile(tmp_path: Path) -> None:
    endpoint = _SpeechEndpoint(b"ID3-production-audio")
    generator = OpenAIAudioGenerator(api_key="test-key", client=_client(endpoint))
    destination = tmp_path / "episode.mp3"
    spoken_text = "Grüße, Maria. Ich weiß, dass alles gut wird."

    result = await generator.synthesize(spoken_text, destination)

    assert result == destination
    assert destination.read_bytes() == b"ID3-production-audio"
    assert len(endpoint.calls) == 1
    call = endpoint.calls[0]
    assert call["input"] == spoken_text
    assert call["model"] == "gpt-4o-mini-tts"
    assert call["voice"] == "marin"
    assert call["response_format"] == "mp3"
    assert call["speed"] == 1.0
    assert "clean Hochdeutsch" in call["instructions"]


@pytest.mark.asyncio
async def test_openai_tts_calls_provider_again_for_existing_destination(tmp_path: Path) -> None:
    endpoint = _SpeechEndpoint(b"ID3-new-audio")
    generator = OpenAIAudioGenerator(api_key="test-key", client=_client(endpoint))
    destination = tmp_path / "episode.mp3"

    await generator.synthesize("Heute ist Montag.", destination)
    await generator.synthesize("Heute ist Montag.", destination)

    assert len(endpoint.calls) == 2
    assert destination.read_bytes() == b"ID3-new-audio"
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.asyncio
async def test_openai_tts_keeps_existing_artifact_when_provider_returns_empty_audio(
    tmp_path: Path,
) -> None:
    endpoint = _SpeechEndpoint(b"")
    generator = OpenAIAudioGenerator(api_key="test-key", client=_client(endpoint))
    destination = tmp_path / "episode.mp3"
    destination.write_bytes(b"previous-valid-audio")

    with pytest.raises(TTSGenerationError, match="empty audio"):
        await generator.synthesize("Heute erzähle ich dir von meinem Tag.", destination)

    assert destination.read_bytes() == b"previous-valid-audio"
    assert list(tmp_path.iterdir()) == [destination]
