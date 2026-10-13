from __future__ import annotations

import asyncio
import json
import re
import subprocess
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from anna_backend.media_types import AlignmentResult, WordTiming

_SUBPROCESS_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class AudioPacingError(RuntimeError):
    """The educational audio timeline could not be produced safely."""


class ReadyAudioEncodingError(RuntimeError):
    """The ready MP3 could not be encoded safely."""


@dataclass(frozen=True, slots=True)
class LearningPace:
    playback_rate: float = 0.95
    pause_after_sentence_seconds: float = 0.10
    intro_silence_seconds: float = 0.20
    target_lufs: float = -14.0
    loudness_range: float = 7.0
    true_peak_db: float = -1.0
    sample_rate: int = 48_000

    def __post_init__(self) -> None:
        if not 0.5 <= self.playback_rate <= 1.2:
            raise ValueError("playback_rate must be between 0.5 and 1.2")
        if not 0.0 <= self.pause_after_sentence_seconds <= 10.0:
            raise ValueError("pause_after_sentence_seconds must be between 0 and 10")
        if not 0.0 <= self.intro_silence_seconds <= 10.0:
            raise ValueError("intro_silence_seconds must be between 0 and 10")
        if self.sample_rate != 48_000:
            raise ValueError("learning audio must use a 48 kHz sample rate")


@dataclass(frozen=True, slots=True)
class LearningTimeline:
    alignment: AlignmentResult
    source_duration_seconds: float
    output_duration_seconds: float
    sentence_count: int
    segment_boundaries: tuple[tuple[float, float], ...]


def _sentence_groups(words: tuple[WordTiming, ...]) -> list[list[WordTiming]]:
    groups: list[list[WordTiming]] = []
    current: list[WordTiming] = []
    for word in words:
        current.append(word)
        if re.search(r"[.!?…][\"'»”)]*$", word.word):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _segment_boundaries(
    sentences: list[list[WordTiming]], source_duration_seconds: float
) -> tuple[tuple[float, float], ...]:
    if not sentences:
        raise ValueError("alignment must contain at least one sentence")
    if source_duration_seconds < sentences[-1][-1].end_seconds:
        raise ValueError("source audio ends before the final aligned word")

    boundaries: list[tuple[float, float]] = []
    start = 0.0
    for index, sentence in enumerate(sentences):
        if index + 1 < len(sentences):
            current_end = sentence[-1].end_seconds
            next_start = sentences[index + 1][0].start_seconds
            end = current_end + max(0.0, next_start - current_end) / 2.0
        else:
            end = source_duration_seconds
        if end <= start:
            raise ValueError("sentence audio segments must have positive duration")
        boundaries.append((start, end))
        start = end
    return tuple(boundaries)


def build_learning_timeline(
    alignment: AlignmentResult,
    *,
    source_duration_seconds: float,
    pace: LearningPace | None = None,
    segment_boundaries: tuple[tuple[float, float], ...] | None = None,
) -> LearningTimeline:
    profile = pace or LearningPace()
    sentences = _sentence_groups(alignment.words)
    boundaries = segment_boundaries or _segment_boundaries(sentences, source_duration_seconds)
    if len(boundaries) != len(sentences):
        raise ValueError("segment boundary count must match sentence count")
    retimed_words: list[WordTiming] = []
    output_cursor = profile.intro_silence_seconds

    for index, (sentence, (segment_start, segment_end)) in enumerate(
        zip(sentences, boundaries, strict=True)
    ):
        for word in sentence:
            clamped_start = min(max(word.start_seconds, segment_start), segment_end - 0.01)
            clamped_end = min(max(word.end_seconds, clamped_start + 0.01), segment_end)
            retimed_words.append(
                WordTiming(
                    word=word.word,
                    start_seconds=output_cursor
                    + (clamped_start - segment_start) / profile.playback_rate,
                    end_seconds=output_cursor
                    + (clamped_end - segment_start) / profile.playback_rate,
                )
            )
        output_cursor += (segment_end - segment_start) / profile.playback_rate
        if index + 1 < len(sentences):
            output_cursor += profile.pause_after_sentence_seconds

    return LearningTimeline(
        alignment=AlignmentResult(words=tuple(retimed_words), score=alignment.score),
        source_duration_seconds=source_duration_seconds,
        output_duration_seconds=output_cursor,
        sentence_count=len(sentences),
        segment_boundaries=boundaries,
    )


def _run(command: list[str], *, timeout: int = 300) -> str:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            creationflags=_SUBPROCESS_CREATION_FLAGS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AudioPacingError(f"Could not run {command[0]}: {error}") from error
    if result.returncode != 0:
        diagnostic = (result.stderr or result.stdout or "no diagnostic output").strip()
        raise AudioPacingError(
            f"{command[0]} failed with exit code {result.returncode}: {diagnostic}"
        )
    return result.stdout


def _audio_duration(ffprobe_path: str, source: Path) -> float:
    output = _run(
        [
            ffprobe_path,
            "-v",
            "error",
            "-show_format",
            "-of",
            "json",
            str(source.resolve()),
        ]
    )
    try:
        payload: dict[str, Any] = json.loads(output)
        raw_duration: object = (payload.get("format") or {}).get("duration")
    except (json.JSONDecodeError, AttributeError) as error:
        raise AudioPacingError(f"FFprobe returned no valid duration for {source}") from error
    if not isinstance(raw_duration, (int, float, str)):
        raise AudioPacingError(f"FFprobe returned no valid duration for {source}")
    try:
        duration = float(raw_duration)
    except ValueError as error:
        raise AudioPacingError(f"FFprobe returned no valid duration for {source}") from error
    if duration <= 0:
        raise AudioPacingError(f"Audio duration must be positive for {source}")
    return duration


def _silence_intervals(ffmpeg_path: str, source: Path) -> tuple[tuple[float, float], ...]:
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-i",
        str(source.resolve()),
        "-af",
        "silencedetect=noise=-38dB:d=0.12",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            shell=False,
            creationflags=_SUBPROCESS_CREATION_FLAGS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AudioPacingError(f"Could not inspect source silences: {error}") from error
    diagnostic = result.stderr or result.stdout
    if result.returncode != 0:
        raise AudioPacingError(f"FFmpeg silence detection failed: {diagnostic.strip()}")
    starts = [float(value) for value in re.findall(r"silence_start:\s*([0-9.]+)", diagnostic)]
    ends = [float(value) for value in re.findall(r"silence_end:\s*([0-9.]+)", diagnostic)]
    if len(starts) != len(ends):
        raise AudioPacingError("FFmpeg returned incomplete silence intervals")
    return tuple(zip(starts, ends, strict=True))


def _align_boundaries_to_silence(
    reference: tuple[tuple[float, float], ...],
    silences: tuple[tuple[float, float], ...],
) -> tuple[tuple[float, float], ...]:
    if len(reference) <= 1:
        return reference
    selected: list[float] = []
    for _, target in reference[:-1]:
        candidates = [
            (abs((start + end) / 2.0 - target), start, end)
            for start, end in silences
            if end - start >= 0.20 and abs((start + end) / 2.0 - target) <= 0.75
        ]
        if not candidates:
            selected.append(target)
            continue
        _, start, end = min(candidates)
        # Cut near the end of real silence, but retain a guard before the next word attack.
        selected.append(max(start + 0.02, end - 0.05))

    edges = [0.0, *selected, reference[-1][1]]
    if any(right <= left for left, right in pairwise(edges)):
        raise AudioPacingError("Silence-aligned sentence boundaries are not monotonic")
    return tuple(pairwise(edges))


def _filter_graph(timeline: LearningTimeline, pace: LearningPace) -> str:
    segment_count = len(timeline.segment_boundaries)
    split_labels = "".join(f"[source{index}]" for index in range(segment_count))
    filters = [f"[0:a]asplit={segment_count}{split_labels}"]
    concat_labels: list[str] = []

    if pace.intro_silence_seconds > 0:
        filters.append(
            f"anullsrc=r={pace.sample_rate}:cl=stereo,"
            f"atrim=duration={pace.intro_silence_seconds:.6f}[intro]"
        )
        concat_labels.append("[intro]")

    for index, (start, end) in enumerate(timeline.segment_boundaries):
        filters.append(
            f"[source{index}]atrim=start={start:.6f}:end={end:.6f},"
            "asetpts=PTS-STARTPTS,"
            f"atempo={pace.playback_rate:.6f},"
            f"aresample={pace.sample_rate},aformat=channel_layouts=stereo[spoken{index}]"
        )
        concat_labels.append(f"[spoken{index}]")
        if index + 1 < segment_count and pace.pause_after_sentence_seconds > 0:
            filters.append(
                f"anullsrc=r={pace.sample_rate}:cl=stereo,"
                f"atrim=duration={pace.pause_after_sentence_seconds:.6f}[pause{index}]"
            )
            concat_labels.append(f"[pause{index}]")

    filters.append(
        f"{''.join(concat_labels)}concat=n={len(concat_labels)}:v=0:a=1,"
        f"loudnorm=I={pace.target_lufs:.1f}:LRA={pace.loudness_range:.1f}:"
        f"TP={pace.true_peak_db:.1f},aresample={pace.sample_rate}[paced]"
    )
    return ";".join(filters)


class FFmpegReadyAudioEncoder:
    def __init__(self, *, ffmpeg_path: str = "ffmpeg") -> None:
        self.ffmpeg_path = ffmpeg_path

    def _encode_sync(self, *, source: Path, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(
                [
                    self.ffmpeg_path,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-y",
                    "-i",
                    str(source.resolve()),
                    "-map_metadata",
                    "-1",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "256k",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    str(destination.resolve()),
                ],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
                creationflags=_SUBPROCESS_CREATION_FLAGS,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ReadyAudioEncodingError(
                f"Could not run ready-MP3 encoding: {error}"
            ) from error
        if completed.returncode != 0:
            detail = completed.stderr.strip()[-800:]
            raise ReadyAudioEncodingError(f"FFmpeg ready-MP3 encoding failed: {detail}")
        if not destination.is_file() or destination.stat().st_size <= 0:
            raise ReadyAudioEncodingError(
                f"ready MP3 candidate is missing or empty: {destination}"
            )
        return destination

    async def encode(self, *, source: Path, destination: Path) -> Path:
        return await asyncio.to_thread(
            self._encode_sync,
            source=source,
            destination=destination,
        )


class FFmpegLearningAudioPacer:
    def __init__(
        self,
        *,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        pace: LearningPace | None = None,
    ) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self._pace = pace or LearningPace()

    def _build_sync(
        self,
        *,
        source: Path,
        alignment: AlignmentResult,
        destination: Path,
    ) -> LearningTimeline:
        if not source.is_file() or source.stat().st_size <= 0:
            raise FileNotFoundError(f"source audio is missing or empty: {source}")
        if destination.suffix.casefold() != ".wav":
            raise ValueError("paced intermediate audio must use a .wav extension")
        source_duration = _audio_duration(self.ffprobe_path, source)
        sentences = _sentence_groups(alignment.words)
        reference_boundaries = _segment_boundaries(sentences, source_duration)
        boundaries = _align_boundaries_to_silence(
            reference_boundaries,
            _silence_intervals(self.ffmpeg_path, source),
        )
        timeline = build_learning_timeline(
            alignment,
            source_duration_seconds=source_duration,
            pace=self._pace,
            segment_boundaries=boundaries,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                self.ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-i",
                str(source.resolve()),
                "-filter_complex",
                _filter_graph(timeline, self._pace),
                "-map",
                "[paced]",
                "-c:a",
                "pcm_s16le",
                "-ar",
                str(self._pace.sample_rate),
                "-ac",
                "2",
                str(destination.resolve()),
            ]
        )
        output_duration = _audio_duration(self.ffprobe_path, destination)
        if abs(output_duration - timeline.output_duration_seconds) > 0.20:
            raise AudioPacingError(
                "Paced audio duration differs from the deterministic learning timeline: "
                f"expected {timeline.output_duration_seconds:.3f}s, received {output_duration:.3f}s"
            )
        return timeline

    async def pace(
        self,
        *,
        source: Path,
        alignment: AlignmentResult,
        destination: Path,
    ) -> AlignmentResult:
        timeline = await asyncio.to_thread(
            self._build_sync,
            source=source,
            alignment=alignment,
            destination=destination,
        )
        return timeline.alignment
