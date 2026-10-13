from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from anna_backend.alignment.aligner import format_alignment_diagnostics
from anna_backend.audio.learning import ReadyAudioEncodingError
from anna_backend.local_pipeline import LocalPipeline, LocalPipelineError
from anna_backend.media_types import AlignmentResult, WordTiming
from anna_backend.video.ffmpeg import MediaProcessError

_OUTPUT_NAME = "lesson-004"
_TOPIC = "Ein guter Tag"
_VIDEO_SHA256 = "f" * 64
_MATCHING_WORDS = (
    WordTiming("Heute", 0.0, 0.2),
    WordTiming("ist", 0.2, 0.4),
    WordTiming("ein", 0.4, 0.6),
    WordTiming("guter", 0.6, 0.8),
    WordTiming("Tag", 0.8, 1.0),
)
_MISMATCHING_WORDS = (
    WordTiming("completely", 0.0, 0.5),
    WordTiming("different", 0.5, 1.0),
)


def test_alignment_diagnostics_reports_non_lexical_whisper_word() -> None:
    report = format_alignment_diagnostics(
        "Heute ist ein guter Tag.",
        (
            WordTiming("Heute", 0.0, 0.2),
            WordTiming("—", 0.2, 0.3),
            WordTiming("Tag", 0.3, 0.5),
        ),
    )

    assert "whisper_transcript='Heute — Tag'" in report
    assert "ignored_non_lexical_words=[index=1 heard='—']" in report
    assert "diagnostic_score=" in report


def test_alignment_ignores_non_lexical_whisper_words() -> None:
    from anna_backend.alignment.aligner import align_spoken_text

    result = align_spoken_text(
        "Привіт світ.",
        (
            WordTiming("Привіт", 0.0, 0.2),
            WordTiming("—", 0.2, 0.3),
            WordTiming("світ.", 0.3, 0.5),
        ),
    )

    assert result.score == pytest.approx(100.0)
    assert [word.word for word in result.words] == ["Привіт", "світ."]


def test_alignment_treats_ukrainian_i_and_y_as_same_whisper_variant() -> None:
    from anna_backend.alignment.aligner import align_spoken_text

    result = align_spoken_text(
        "Привіт й світе.",
        (
            WordTiming("Привіт", 0.0, 0.2),
            WordTiming("і", 0.2, 0.3),
            WordTiming("світе.", 0.3, 0.5),
        ),
    )

    assert result.score == pytest.approx(100.0)


@dataclass
class _PipelineFakes:
    events: list[str] = field(default_factory=list)
    tts_texts: list[str] = field(default_factory=list)
    whisper_strategies: list[str] = field(default_factory=list)
    ready_snapshots_at_validation: list[tuple[bytes | None, bytes | None]] = field(
        default_factory=list
    )


def _read_if_present(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def _install_pipeline_fakes(
    *,
    transcripts: list[tuple[WordTiming, ...]],
    ready_audio_path: Path,
    ready_video_path: Path,
    validation_error: Exception | None = None,
    encoding_error: Exception | None = None,
) -> tuple[_PipelineFakes, LocalPipeline]:
    fakes = _PipelineFakes()
    remaining_transcripts = iter(transcripts)

    class _FakeTextToSpeech:
        async def synthesize(self, spoken_text: str, destination: Path) -> Path:
            fakes.events.append("tts")
            fakes.tts_texts.append(spoken_text)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"raw-tts-audio")
            return destination

    class _FakeTranscriber:
        async def transcribe(self, audio_path: Path, *, strategy: str) -> tuple[WordTiming, ...]:
            assert audio_path.read_bytes() == b"raw-tts-audio"
            fakes.events.append(f"whisper:{strategy}")
            fakes.whisper_strategies.append(strategy)
            return next(remaining_transcripts)

    class _AudioPacer:
        async def pace(
            self,
            *,
            source: Path,
            alignment: AlignmentResult,
            destination: Path,
        ) -> AlignmentResult:
            assert source.read_bytes() == b"raw-tts-audio"
            fakes.events.append("pace")
            destination.write_bytes(b"paced-wave-audio")
            return alignment

    class _ReadyAudioEncoder:
        async def encode(self, *, source: Path, destination: Path) -> Path:
            assert source.read_bytes() == b"paced-wave-audio"
            fakes.events.append("encode-mp3")
            if encoding_error is not None:
                raise encoding_error
            destination.write_bytes(b"candidate-mp3")
            return destination

    class _SubtitleRenderer:
        async def render(self, alignment: AlignmentResult, destination: Path) -> Path:
            assert alignment.score >= 97.0
            fakes.events.append("subtitles")
            destination.write_text("[Script Info]\n", encoding="utf-8")
            return destination

    class _FakeVideoRenderer:
        async def render(
            self,
            *,
            background: Path,
            audio: Path,
            subtitles: Path,
            destination: Path,
            intro_topic: str = "",
        ) -> Path:
            assert background.read_bytes() == b"background"
            assert audio.read_bytes() == b"paced-wave-audio"
            assert subtitles.is_file()
            assert intro_topic == _TOPIC
            fakes.events.append("render")
            destination.write_bytes(b"candidate-video")
            return destination

        async def validate(
            self,
            video_path: Path,
            *,
            audio_path: Path,
            expected_audio_codec: str = "aac",
            expected_audio_sample_rate: int | None = None,
        ) -> SimpleNamespace:
            assert video_path.read_bytes() == b"candidate-video"
            assert audio_path.read_bytes() == b"candidate-mp3"
            assert expected_audio_codec == "aac"
            assert expected_audio_sample_rate is None
            fakes.events.append("validate")
            fakes.ready_snapshots_at_validation.append(
                (
                    _read_if_present(ready_audio_path),
                    _read_if_present(ready_video_path),
                )
            )
            if validation_error is not None:
                raise validation_error
            return SimpleNamespace(video_sha256=_VIDEO_SHA256)

    pipeline = LocalPipeline(
        text_to_speech=_FakeTextToSpeech(),
        transcriber=_FakeTranscriber(),
        audio_pacer=_AudioPacer(),
        ready_audio_encoder=_ReadyAudioEncoder(),
        subtitle_renderer=_SubtitleRenderer(),
        video_renderer=_FakeVideoRenderer(),
    )
    return fakes, pipeline


def _local_paths(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    background = tmp_path / "background.png"
    background.write_bytes(b"background")
    ready_audio_dir = tmp_path / "ready-audio"
    ready_video_dir = tmp_path / "ready-videos"
    tmp_dir = tmp_path / "tmp"
    return background, ready_audio_dir, ready_video_dir, tmp_dir


def _assert_per_run_workspace_clean(tmp_dir: Path) -> None:
    assert not tmp_dir.exists() or list(tmp_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_success_publishes_only_after_validation_and_calls_tts_once(
    tmp_path: Path,
) -> None:
    background, ready_audio_dir, ready_video_dir, tmp_dir = _local_paths(tmp_path)
    ready_audio_path = ready_audio_dir / f"{_OUTPUT_NAME}.mp3"
    ready_video_path = ready_video_dir / f"{_OUTPUT_NAME}.mp4"
    fakes, pipeline = _install_pipeline_fakes(
        transcripts=[_MATCHING_WORDS],
        ready_audio_path=ready_audio_path,
        ready_video_path=ready_video_path,
    )

    result = await pipeline.run(
        text="Heute ist ein guter Tag.",
        topic=_TOPIC,
        background=background,
        output_name=_OUTPUT_NAME,
        ready_audio_dir=ready_audio_dir,
        ready_video_dir=ready_video_dir,
        tmp_dir=tmp_dir,
    )

    assert fakes.tts_texts == ["Heute ist ein guter Tag."]
    assert fakes.whisper_strategies == ["word"]
    assert fakes.ready_snapshots_at_validation == [(None, None)]
    assert fakes.events.index("validate") > fakes.events.index("render")
    assert ready_audio_path.read_bytes() == b"candidate-mp3"
    assert ready_video_path.read_bytes() == b"candidate-video"
    assert result.audio_path == ready_audio_path
    assert result.video_path == ready_video_path
    assert result.alignment_score == pytest.approx(100.0)
    assert result.whisper_strategy == "word"
    assert result.video_sha256 == _VIDEO_SHA256
    _assert_per_run_workspace_clean(tmp_dir)


@pytest.mark.asyncio
async def test_normalizes_multiline_text_and_punctuation_before_tts(
    tmp_path: Path,
) -> None:
    background, ready_audio_dir, ready_video_dir, tmp_dir = _local_paths(tmp_path)
    transcript = (*_MATCHING_WORDS, WordTiming("Heute", 1.0, 1.2))
    fakes, pipeline = _install_pipeline_fakes(
        transcripts=[transcript],
        ready_audio_path=ready_audio_dir / f"{_OUTPUT_NAME}.mp3",
        ready_video_path=ready_video_dir / f"{_OUTPUT_NAME}.mp4",
    )

    result = await pipeline.run(
        text="Heute\n(\nist\n)\nein\n–\nguter\nTag.Heute",
        topic=_TOPIC,
        background=background,
        output_name=_OUTPUT_NAME,
        ready_audio_dir=ready_audio_dir,
        ready_video_dir=ready_video_dir,
        tmp_dir=tmp_dir,
    )

    assert fakes.tts_texts == ["Heute ist ein guter Tag. Heute"]
    assert result.alignment_score == pytest.approx(100.0)


def test_normalizes_emoji_out_of_spoken_text() -> None:
    from anna_backend.local_pipeline import _normalize_spoken_text

    assert _normalize_spoken_text("Ранок🍃 корисний 🙂 день") == ("Ранок корисний день")


@pytest.mark.asyncio
async def test_multiline_ukrainian_text_preserves_cyrillic_during_normalization(
    tmp_path: Path,
) -> None:
    background, ready_audio_dir, ready_video_dir, tmp_dir = _local_paths(tmp_path)
    words = ("Сьогодні", "ми", "вивчаємо", "українську", "мову")
    transcript = tuple(
        WordTiming(word, index * 0.2, (index + 1) * 0.2) for index, word in enumerate(words)
    )
    fakes, pipeline = _install_pipeline_fakes(
        transcripts=[transcript],
        ready_audio_path=ready_audio_dir / f"{_OUTPUT_NAME}.mp3",
        ready_video_path=ready_video_dir / f"{_OUTPUT_NAME}.mp4",
    )

    result = await pipeline.run(
        text="Сьогодні\nми вивчаємо\nукраїнську мову.",
        topic=_TOPIC,
        background=background,
        output_name=_OUTPUT_NAME,
        ready_audio_dir=ready_audio_dir,
        ready_video_dir=ready_video_dir,
        tmp_dir=tmp_dir,
    )

    assert fakes.tts_texts == ["Сьогодні ми вивчаємо українську мову."]
    assert result.alignment_score == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_first_low_alignment_uses_one_alternate_whisper_without_tts_retry(
    tmp_path: Path,
) -> None:
    background, ready_audio_dir, ready_video_dir, tmp_dir = _local_paths(tmp_path)
    fakes, pipeline = _install_pipeline_fakes(
        transcripts=[_MISMATCHING_WORDS, _MATCHING_WORDS],
        ready_audio_path=ready_audio_dir / f"{_OUTPUT_NAME}.mp3",
        ready_video_path=ready_video_dir / f"{_OUTPUT_NAME}.mp4",
    )

    result = await pipeline.run(
        text="Heute ist ein guter Tag.",
        topic=_TOPIC,
        background=background,
        output_name=_OUTPUT_NAME,
        ready_audio_dir=ready_audio_dir,
        ready_video_dir=ready_video_dir,
        tmp_dir=tmp_dir,
    )

    assert fakes.tts_texts == ["Heute ist ein guter Tag."]
    assert fakes.whisper_strategies == ["word", "sentence"]
    assert result.whisper_strategy == "sentence"
    assert result.alignment_score == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_two_low_alignments_require_manual_rerun_and_preserve_ready_artifacts(
    tmp_path: Path,
) -> None:
    background, ready_audio_dir, ready_video_dir, tmp_dir = _local_paths(tmp_path)
    ready_audio_dir.mkdir()
    ready_video_dir.mkdir()
    ready_audio_path = ready_audio_dir / f"{_OUTPUT_NAME}.mp3"
    ready_video_path = ready_video_dir / f"{_OUTPUT_NAME}.mp4"
    ready_audio_path.write_bytes(b"existing-audio")
    ready_video_path.write_bytes(b"existing-video")
    fakes, pipeline = _install_pipeline_fakes(
        transcripts=[_MISMATCHING_WORDS, _MISMATCHING_WORDS],
        ready_audio_path=ready_audio_path,
        ready_video_path=ready_video_path,
    )

    with pytest.raises(LocalPipelineError) as exc_info:
        await pipeline.run(
            text="Heute ist ein guter Tag.",
            topic=_TOPIC,
            background=background,
            output_name=_OUTPUT_NAME,
            ready_audio_dir=ready_audio_dir,
            ready_video_dir=ready_video_dir,
            tmp_dir=tmp_dir,
        )

    message = str(exc_info.value).lower()
    assert "alignment" in message
    assert "97" in message
    assert "manual" in message
    assert "rerun" in message or "run again" in message
    assert fakes.tts_texts == ["Heute ist ein guter Tag."]
    assert fakes.whisper_strategies == ["word", "sentence"]
    assert "whisper_transcript='completely different'" in message
    assert "expected='" in message
    assert "heard='<missing>'" in message
    assert "similarity=" in message
    assert ready_audio_path.read_bytes() == b"existing-audio"
    assert ready_video_path.read_bytes() == b"existing-video"
    assert "pace" not in fakes.events
    _assert_per_run_workspace_clean(tmp_dir)


@pytest.mark.asyncio
async def test_validator_failure_preserves_preexisting_ready_artifacts(
    tmp_path: Path,
) -> None:
    background, ready_audio_dir, ready_video_dir, tmp_dir = _local_paths(tmp_path)
    ready_audio_dir.mkdir()
    ready_video_dir.mkdir()
    ready_audio_path = ready_audio_dir / f"{_OUTPUT_NAME}.mp3"
    ready_video_path = ready_video_dir / f"{_OUTPUT_NAME}.mp4"
    ready_audio_path.write_bytes(b"existing-audio")
    ready_video_path.write_bytes(b"existing-video")
    fakes, pipeline = _install_pipeline_fakes(
        transcripts=[_MATCHING_WORDS],
        ready_audio_path=ready_audio_path,
        ready_video_path=ready_video_path,
        validation_error=MediaProcessError("validator rejected candidate"),
    )

    with pytest.raises(MediaProcessError, match="validator rejected candidate"):
        await pipeline.run(
            text="Heute ist ein guter Tag.",
            topic=_TOPIC,
            background=background,
            output_name=_OUTPUT_NAME,
            ready_audio_dir=ready_audio_dir,
            ready_video_dir=ready_video_dir,
            tmp_dir=tmp_dir,
        )

    assert fakes.ready_snapshots_at_validation == [(b"existing-audio", b"existing-video")]
    assert ready_audio_path.read_bytes() == b"existing-audio"
    assert ready_video_path.read_bytes() == b"existing-video"
    _assert_per_run_workspace_clean(tmp_dir)


@pytest.mark.asyncio
async def test_blank_text_is_rejected_before_paid_calls(
    tmp_path: Path,
) -> None:
    background, ready_audio_dir, ready_video_dir, tmp_dir = _local_paths(tmp_path)
    fakes, pipeline = _install_pipeline_fakes(
        transcripts=[],
        ready_audio_path=ready_audio_dir / f"{_OUTPUT_NAME}.mp3",
        ready_video_path=ready_video_dir / f"{_OUTPUT_NAME}.mp4",
    )

    with pytest.raises(ValueError, match=r"(?i)text.*empty"):
        await pipeline.run(
            text=" \n\t ",
            topic=_TOPIC,
            background=background,
            output_name=_OUTPUT_NAME,
            ready_audio_dir=ready_audio_dir,
            ready_video_dir=ready_video_dir,
            tmp_dir=tmp_dir,
        )

    assert fakes.tts_texts == []
    assert fakes.whisper_strategies == []
    assert fakes.events == []


@pytest.mark.asyncio
async def test_ready_audio_encoding_error_passes_through_pipeline_unchanged(
    tmp_path: Path,
) -> None:
    background, ready_audio_dir, ready_video_dir, tmp_dir = _local_paths(tmp_path)
    ready_audio_dir.mkdir()
    ready_video_dir.mkdir()
    ready_audio_path = ready_audio_dir / f"{_OUTPUT_NAME}.mp3"
    ready_video_path = ready_video_dir / f"{_OUTPUT_NAME}.mp4"
    ready_audio_path.write_bytes(b"existing-audio")
    ready_video_path.write_bytes(b"existing-video")
    expected = ReadyAudioEncodingError("ready audio failed")
    fakes, pipeline = _install_pipeline_fakes(
        transcripts=[_MATCHING_WORDS],
        ready_audio_path=ready_audio_path,
        ready_video_path=ready_video_path,
        encoding_error=expected,
    )

    with pytest.raises(ReadyAudioEncodingError) as exc_info:
        await pipeline.run(
            text="Heute ist ein guter Tag.",
            topic=_TOPIC,
            background=background,
            output_name=_OUTPUT_NAME,
            ready_audio_dir=ready_audio_dir,
            ready_video_dir=ready_video_dir,
            tmp_dir=tmp_dir,
        )

    assert exc_info.value is expected
    report = pipeline.last_log_path.read_text(encoding="utf-8")
    assert "FAIL ready_audio_encoder.encode" in report
    assert "ReadyAudioEncodingError: ready audio failed" in report
    assert "Traceback (most recent call last)" in report
    assert "pace" in fakes.events
    assert ready_audio_path.read_bytes() == b"existing-audio"
    assert ready_video_path.read_bytes() == b"existing-video"
    _assert_per_run_workspace_clean(tmp_dir)


@pytest.mark.asyncio
async def test_rejects_tag_prefix_before_provider_calls(
    tmp_path: Path,
) -> None:
    background, ready_audio_dir, ready_video_dir, tmp_dir = _local_paths(tmp_path)
    fakes, pipeline = _install_pipeline_fakes(
        transcripts=[],
        ready_audio_path=ready_audio_dir / f"{_OUTPUT_NAME}.mp3",
        ready_video_path=ready_video_dir / f"{_OUTPUT_NAME}.mp4",
    )

    with pytest.raises(ValueError, match="Tag N"):
        await pipeline.run(
            text="Heute ist ein guter Tag.",
            topic="Tag 12: Beim Bäcker bestellen",
            background=background,
            output_name=_OUTPUT_NAME,
            ready_audio_dir=ready_audio_dir,
            ready_video_dir=ready_video_dir,
            tmp_dir=tmp_dir,
        )

    assert fakes.tts_texts == []
    assert fakes.whisper_strategies == []
    assert fakes.events == []
    _assert_per_run_workspace_clean(tmp_dir)
