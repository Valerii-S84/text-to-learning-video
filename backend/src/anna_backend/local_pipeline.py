from __future__ import annotations

import os
import re
import shutil
import unicodedata
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Protocol

from anna_backend.alignment.aligner import (
    AlignmentError,
    DeterministicTextAligner,
    format_alignment_diagnostics,
)
from anna_backend.execution_log import logged_run, operation, traced
from anna_backend.media_types import (
    AlignmentResult,
    LocalMediaResult,
    VideoValidationResult,
    WordTiming,
)

ALIGNMENT_MIN_SCORE = 97.0
WHISPER_STRATEGIES = ("word", "sentence")


class TextToSpeech(Protocol):
    async def synthesize(self, spoken_text: str, destination: Path) -> Path: ...


class Transcriber(Protocol):
    async def transcribe(
        self,
        audio_path: Path,
        *,
        strategy: str = "word",
    ) -> tuple[WordTiming, ...]: ...


class AudioPacer(Protocol):
    async def pace(
        self,
        *,
        source: Path,
        alignment: AlignmentResult,
        destination: Path,
    ) -> AlignmentResult: ...


class ReadyAudioEncoder(Protocol):
    async def encode(
        self,
        *,
        source: Path,
        destination: Path,
    ) -> Path: ...


class SubtitleRenderer(Protocol):
    async def render(
        self,
        alignment: AlignmentResult,
        destination: Path,
    ) -> Path: ...


class VideoRenderer(Protocol):
    async def render(
        self,
        *,
        background: Path,
        audio: Path,
        subtitles: Path,
        destination: Path,
        intro_topic: str = "",
    ) -> Path: ...

    async def validate(
        self,
        video_path: Path,
        *,
        audio_path: Path,
        expected_audio_codec: str = "aac",
        expected_audio_sample_rate: int | None = None,
    ) -> VideoValidationResult: ...


class LocalPipeline:
    def __init__(
        self,
        *,
        text_to_speech: TextToSpeech,
        transcriber: Transcriber,
        audio_pacer: AudioPacer,
        ready_audio_encoder: ReadyAudioEncoder,
        subtitle_renderer: SubtitleRenderer,
        video_renderer: VideoRenderer,
    ) -> None:
        self._text_to_speech = text_to_speech
        self._transcriber = transcriber
        self._audio_pacer = audio_pacer
        self._ready_audio_encoder = ready_audio_encoder
        self._subtitle_renderer = subtitle_renderer
        self._video_renderer = video_renderer

    @logged_run
    async def run(
        self,
        *,
        text: str,
        topic: str,
        background: Path,
        output_name: str,
        ready_audio_dir: Path,
        ready_video_dir: Path,
        tmp_dir: Path,
    ) -> LocalMediaResult:
        """Run one bounded local media generation and publish validated artifacts."""

        text = _normalize_spoken_text(text)
        background, safe_name = _validate_inputs(
            text=text,
            topic=topic,
            background=background,
            output_name=output_name,
        )
        resolved_ready_audio = ready_audio_dir.expanduser().resolve()
        resolved_ready_video = ready_video_dir.expanduser().resolve()
        resolved_tmp = tmp_dir.expanduser().resolve()
        resolved_tmp.mkdir(parents=True, exist_ok=True)
        aligner = DeterministicTextAligner(minimum_score=ALIGNMENT_MIN_SCORE)

        with TemporaryDirectory(
            prefix=f"make-video-{safe_name}-", dir=resolved_tmp
        ) as workspace_name:
            workspace = Path(workspace_name)
            source_audio = workspace / "source.mp3"
            paced_audio = workspace / "paced.wav"
            subtitles = workspace / "karaoke.ass"
            ready_audio_candidate = workspace / f"{safe_name}.mp3"
            video_candidate = workspace / f"{safe_name}.mp4"

            # Operation 1: exactly one TTS synthesis per local run.
            with operation('self._text_to_speech.synthesize'):
                await self._text_to_speech.synthesize(text, source_audio)
            _require_non_empty(source_audio, "TTS source audio")

            # Operation 2: Whisper plus deterministic alignment, with one alternate.
            alignment, whisper_strategy = await _transcribe_and_align(
                text=text,
                source_audio=source_audio,
                transcriber=self._transcriber,
                aligner=aligner,
            )

            # Operation 3: learning pace, karaoke ASS, and ready MP3 candidate.
            paced_alignment = await _build_learning_artifacts(
                source_audio=source_audio,
                alignment=alignment,
                paced_audio=paced_audio,
                subtitles=subtitles,
                ready_audio_candidate=ready_audio_candidate,
                audio_pacer=self._audio_pacer,
                ready_audio_encoder=self._ready_audio_encoder,
                subtitle_renderer=self._subtitle_renderer,
            )

            # Operation 4: render and run the current FFprobe-backed MP4 validator.
            validation = await _render_and_validate(
                topic=topic,
                background=background,
                paced_audio=paced_audio,
                subtitles=subtitles,
                ready_audio_candidate=ready_audio_candidate,
                video_candidate=video_candidate,
                video_renderer=self._video_renderer,
            )

            final_audio = resolved_ready_audio / f"{safe_name}.mp3"
            final_video = resolved_ready_video / f"{safe_name}.mp4"
            resolved_ready_audio.mkdir(parents=True, exist_ok=True)
            resolved_ready_video.mkdir(parents=True, exist_ok=True)
            _publish_pair(ready_audio_candidate, final_audio, video_candidate, final_video)

        return LocalMediaResult(
            audio_path=final_audio,
            video_path=final_video,
            alignment_score=paced_alignment.score,
            whisper_strategy=whisper_strategy,
            video_sha256=str(validation.video_sha256),
        )


class LocalPipelineError(RuntimeError):
    """The local media run could not publish a complete validated result."""


@traced
def _normalize_spoken_text(text: str) -> str:
    text = "".join(
        " "
        if "\U0001f300" <= character <= "\U0001faff"
        or unicodedata.category(character) == "Cf"
        else character
        for character in text
    )
    text = re.sub(r"[()\[\]{}<>–—]", " ", text)
    text = re.sub(r"(?<=[.!?])(?=\w)", " ", text)
    return " ".join(text.split())


@traced
def _validate_inputs(
    *,
    text: str,
    topic: str,
    background: Path,
    output_name: str,
) -> tuple[Path, str]:
    if not text.strip():
        raise ValueError("text must not be empty")
    if not topic.strip():
        raise ValueError("topic must not be empty")
    if re.match(r"^\s*Tag\s+\d+\s*:", topic, re.IGNORECASE):
        raise ValueError(
            "topic must not include a 'Tag N:' prefix; pass only the topic text"
        )

    background = background.expanduser().resolve()
    if not background.is_file() or background.stat().st_size <= 0:
        raise FileNotFoundError(f"background image is missing or empty: {background}")

    name = output_name.strip()
    if not name:
        raise ValueError("output_name must not be empty")
    if Path(name).name != name or name in {".", ".."}:
        raise ValueError("output_name must be a local file name, not a path")
    if Path(name).suffix:
        raise ValueError("output_name must not include a file extension")
    if any(character in '<>:"/\\|?*' or ord(character) < 32 for character in name):
        raise ValueError("output_name contains unsupported file-name characters")
    return background, name


@traced
def _require_non_empty(path: Path, label: str) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise LocalPipelineError(f"{label} is missing or empty: {path}")


@traced
async def _transcribe_and_align(
    *,
    text: str,
    source_audio: Path,
    transcriber: Transcriber,
    aligner: DeterministicTextAligner,
) -> tuple[AlignmentResult, str]:
    failures: list[str] = []
    for strategy in WHISPER_STRATEGIES:
        words: tuple[WordTiming, ...] | None = None
        try:
            with operation(f"transcriber.transcribe strategy={strategy}"):
                words = await transcriber.transcribe(source_audio, strategy=strategy)
            with operation(f"aligner.align strategy={strategy}"):
                alignment = aligner.align(text, words)
            if alignment.score + 1e-9 < ALIGNMENT_MIN_SCORE:
                raise AlignmentError(
                    f"alignment score {alignment.score:.2f}% is below the required "
                    f"{ALIGNMENT_MIN_SCORE:.2f}%"
                )
            return alignment, strategy
        except AlignmentError as error:
            detail = f"{strategy}: {error}"
            if words is not None:
                try:
                    detail = f"{detail}; {format_alignment_diagnostics(text, words)}"
                except AlignmentError as diagnostic_error:
                    detail = f"{detail}; diagnostics unavailable: {diagnostic_error}"
            failures.append(detail)

    detail = "; ".join(failures)
    raise LocalPipelineError(
        "Alignment remained below 97.00% after the primary and one alternate "
        "Whisper strategy. TTS was generated once and was not retried. "
        f"Review the approved text/audio and rerun manually. Details: {detail}"
    )


@traced
async def _build_learning_artifacts(
    *,
    source_audio: Path,
    alignment: AlignmentResult,
    paced_audio: Path,
    subtitles: Path,
    ready_audio_candidate: Path,
    audio_pacer: AudioPacer,
    ready_audio_encoder: ReadyAudioEncoder,
    subtitle_renderer: SubtitleRenderer,
) -> AlignmentResult:
    with operation('audio_pacer.pace'):
        paced_alignment = await audio_pacer.pace(
            source=source_audio,
            alignment=alignment,
            destination=paced_audio,
        )
    with operation('subtitle_renderer.render'):
        await subtitle_renderer.render(paced_alignment, subtitles)
    with operation('ready_audio_encoder.encode'):
        await ready_audio_encoder.encode(
            source=paced_audio,
            destination=ready_audio_candidate,
        )
    return paced_alignment


@traced
async def _render_and_validate(
    *,
    topic: str,
    background: Path,
    paced_audio: Path,
    subtitles: Path,
    ready_audio_candidate: Path,
    video_candidate: Path,
    video_renderer: VideoRenderer,
) -> VideoValidationResult:
    with operation('video_renderer.render'):
        await video_renderer.render(
            background=background,
            audio=paced_audio,
            subtitles=subtitles,
            intro_topic=topic,
            destination=video_candidate,
        )
    _require_non_empty(ready_audio_candidate, "ready MP3 candidate")
    _require_non_empty(video_candidate, "ready MP4 candidate")
    with operation('video_renderer.validate'):
        return await video_renderer.validate(
            video_candidate,
            audio_path=ready_audio_candidate,
        )


@traced
def _publish_pair(audio_candidate: Path, audio: Path, video_candidate: Path, video: Path) -> None:
    backup = None
    if audio.exists():
        with NamedTemporaryFile(prefix=".audio-rollback-", dir=audio.parent, delete=False) as file:
            backup = Path(file.name)
        try:
            shutil.copy2(audio, backup)
        except BaseException:
            backup.unlink(missing_ok=True)
            raise
    audio_published = False
    keep_backup = False
    try:
        with operation("publish_audio"):
            os.replace(audio_candidate, audio)
        audio_published = True
        with operation("publish_video"):
            os.replace(video_candidate, video)
    except BaseException:
        if audio_published:
            try:
                with operation("restore_audio"):
                    if backup is None:
                        audio.unlink()
                    else:
                        os.replace(backup, audio)
            except OSError as error:
                keep_backup = True
                raise LocalPipelineError(
                    f"Publication and audio rollback failed; preserved backup: {backup}"
                ) from error
        raise
    finally:
        if backup is not None and not keep_backup:
            backup.unlink(missing_ok=True)
