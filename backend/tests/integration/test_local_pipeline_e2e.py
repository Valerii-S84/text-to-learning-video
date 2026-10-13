from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from anna_backend.cli import make_video as make_video_module
from anna_backend.media_types import VideoValidationResult, WordTiming
from anna_backend.video.ffmpeg import FFmpegVideoRenderer

BACKEND_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = BACKEND_ROOT / "tests" / "fixtures" / "render"


def _audio_duration(path: Path) -> float:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return float(completed.stdout.strip())


def test_local_fixture_reaches_validated_ready_audio_and_video_through_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = json.loads((FIXTURE_DIR / "sample.json").read_text(encoding="utf-8"))
    approved_text = fixture["content"]["ttsText"]
    frozen_audio = FIXTURE_DIR / "sample.mp3"
    background = BACKEND_ROOT / "resources" / "images" / "Anna.png"
    tts_calls: list[str] = []
    whisper_strategies: list[str] = []

    class FrozenAudioGenerator:
        def __init__(self, **_: object) -> None:
            pass

        async def synthesize(self, spoken_text: str, destination: Path) -> Path:
            tts_calls.append(spoken_text)
            shutil.copyfile(frozen_audio, destination)
            return destination

    class FrozenWordTranscriber:
        def __init__(self, **_: object) -> None:
            pass

        async def transcribe(
            self, audio_path: Path, *, strategy: str
        ) -> tuple[WordTiming, ...]:
            whisper_strategies.append(strategy)
            duration = _audio_duration(audio_path)
            tokens = approved_text.split()
            start_offset = 0.05
            slot = (duration - 0.10) / len(tokens)
            return tuple(
                WordTiming(
                    word=word,
                    start_seconds=start_offset + index * slot,
                    end_seconds=start_offset + (index + 0.82) * slot,
                )
                for index, word in enumerate(tokens)
            )

    ready_audio = tmp_path / "ready-audio"
    ready_video = tmp_path / "ready-videos"
    run_tmp = tmp_path / "tmp"
    monkeypatch.setattr(
        make_video_module, "OpenAIAudioGenerator", FrozenAudioGenerator
    )
    monkeypatch.setattr(
        make_video_module, "OpenAIWhisperWordTranscriber", FrozenWordTranscriber
    )
    monkeypatch.setattr(make_video_module, "DEFAULT_READY_AUDIO_DIR", ready_audio)
    monkeypatch.setattr(make_video_module, "DEFAULT_READY_VIDEO_DIR", ready_video)
    monkeypatch.setattr(make_video_module, "DEFAULT_TMP_DIR", run_tmp)
    monkeypatch.setenv("OPENAI_API_KEY", "frozen-local-test-key")

    exit_code = make_video_module.main(
        [
            "--text",
            approved_text,
            "--topic",
            "Fixture",
            "--background",
            str(background),
            "--output-name",
            "fixture-smoke",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    payload = json.loads(captured.out)
    audio_path = Path(payload["audio_path"])
    video_path = Path(payload["video_path"])

    assert tts_calls == [approved_text]
    assert whisper_strategies in (["word"], ["word", "sentence"])
    assert payload == {
        "audio_path": str(ready_audio / "fixture-smoke.mp3"),
        "video_path": str(ready_video / "fixture-smoke.mp4"),
        "alignment_score": pytest.approx(100.0),
        "whisper_strategy": whisper_strategies[-1],
        "video_sha256": payload["video_sha256"],
    }
    assert audio_path.is_file() and audio_path.stat().st_size > 0
    assert video_path.is_file() and video_path.stat().st_size > 0
    assert list(run_tmp.iterdir()) == []

    validator = FFmpegVideoRenderer(font_dir=make_video_module.DEFAULT_FONT_DIR)
    report = asyncio.run(validator.validate(video_path, audio_path=audio_path))
    assert isinstance(report, VideoValidationResult)
    assert payload["video_sha256"] == report.video_sha256
    assert report.width == 1080
    assert report.height == 1920
    assert report.video_codec == "h264"
    assert report.audio_codec == "aac"
    assert report.video_stream_count == 1
    assert report.audio_stream_count == 1
    assert report.faststart is True
