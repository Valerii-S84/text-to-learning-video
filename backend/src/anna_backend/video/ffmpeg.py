from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from anna_backend.media_types import VideoValidationResult

_SUBPROCESS_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class MediaProcessError(RuntimeError):
    """FFmpeg or FFprobe failed, or the rendered artifact violated the contract."""


@dataclass(frozen=True, slots=True)
class RenderProfile:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    video_codec: str = "libx264"
    video_preset: str = "medium"
    video_crf: int = 20
    audio_bitrate: str = "256k"
    audio_sample_rate: int = 48_000
    duration_seconds: float = 0.0
    minimum_audio_peak_db: float = -12.0
    max_duration_delta_seconds: float = 0.5


_INTRO_TITLE_FONT = "DancingScript-SemiBold.ttf"
_INTRO_TOPIC_FONT = "NotoSans-SemiBold.ttf"
_INTRO_X = 80
_INTRO_TITLE_Y = 102
_INTRO_TOPIC_Y = 214
_INTRO_TITLE_FONT_SIZE = 96
_INTRO_TOPIC_FONT_SIZE = 48
_INTRO_TOPIC_FALLBACK_FONT_SIZE = 42
_INTRO_TOPIC_MAX_WIDTH = 920
_INTRO_TOPIC_LINE_SPACING = 8
_INTRO_TITLE_FONT_COLOR = "0x6B4655"
_INTRO_TOPIC_FONT_COLOR = "0x594A43"
_INTRO_SHADOW = "shadowcolor=black@0.45:shadowx=2:shadowy=2"
_INTRO_FADE_ALPHA = "if(lte(t,2.80),1,if(lt(t,3.00),(3.00-t)/0.20,0))"


def _estimated_topic_width(text: str, font_size: int) -> float:
    narrow = " !'.,:;Iijl|1"
    wide = "@%&MWQGÖÜÄ"
    width_units = 0.0
    for character in text:
        if character in narrow:
            width_units += 0.30
        elif character in wide:
            width_units += 0.86
        elif character.isupper():
            width_units += 0.70
        else:
            width_units += 0.56
    return width_units * font_size


def _two_line_topic(topic: str, font_size: int) -> tuple[str, bool]:
    words = topic.split()
    if len(words) < 2:
        return topic, _estimated_topic_width(topic, font_size) <= _INTRO_TOPIC_MAX_WIDTH
    candidates: list[tuple[float, float, str]] = []
    for split_at in (min(3, len(words) - 1),):
        first_line = " ".join(words[:split_at])
        second_line = " ".join(words[split_at:])
        first_width = _estimated_topic_width(first_line, font_size)
        second_width = _estimated_topic_width(second_line, font_size)
        candidates.append(
            (
                max(first_width, second_width),
                abs(first_width - second_width),
                f"{first_line}\n{second_line}",
            )
        )
    maximum_width, _, wrapped = min(candidates)
    return wrapped, maximum_width <= _INTRO_TOPIC_MAX_WIDTH


def _layout_intro_topic(topic: str) -> tuple[str, int]:
    normalized = " ".join(topic.split())
    if (
        len(normalized.split()) <= 3
        and _estimated_topic_width(normalized, _INTRO_TOPIC_FONT_SIZE)
        <= _INTRO_TOPIC_MAX_WIDTH
    ):
        return normalized, _INTRO_TOPIC_FONT_SIZE
    wrapped, fits = _two_line_topic(normalized, _INTRO_TOPIC_FONT_SIZE)
    if fits:
        return wrapped, _INTRO_TOPIC_FONT_SIZE
    wrapped, _ = _two_line_topic(normalized, _INTRO_TOPIC_FALLBACK_FONT_SIZE)
    return wrapped, _INTRO_TOPIC_FALLBACK_FONT_SIZE


def _escape_drawtext_text(text: str) -> str:
    return (
        text.replace("\\", r"\\\\")
        .replace("'", r"\'")
        .replace(":", r"\:")
        .replace("%", r"\%")
    )


def _escape_drawtext_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], *, cwd: Path | None = None, timeout: int = 300) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
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
        raise MediaProcessError(f"Could not run {command[0]}: {error}") from error
    if result.returncode != 0:
        diagnostic = (result.stderr or result.stdout or "no diagnostic output").strip()
        raise MediaProcessError(
            f"{command[0]} failed with exit code {result.returncode}: {diagnostic}"
        )
    return result.stdout


def _probe_json(ffprobe_path: str, path: Path) -> dict[str, Any]:
    output = _run(
        [
            ffprobe_path,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path.resolve()),
        ]
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise MediaProcessError(f"FFprobe returned invalid JSON for {path}") from error
    if not isinstance(payload, dict):
        raise MediaProcessError(f"FFprobe returned an invalid document for {path}")
    return payload


def _decoded_audio_levels(ffmpeg_path: str, path: Path) -> tuple[int, float, float]:
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-i",
        str(path.resolve()),
        "-map",
        "0:a:0",
        "-af",
        "volumedetect",
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
        raise MediaProcessError(
            f"Could not decode final audio with {ffmpeg_path}: {error}"
        ) from error
    diagnostic = result.stderr or result.stdout
    if result.returncode != 0:
        raise MediaProcessError(
            f"Final audio decode failed with exit code {result.returncode}: {diagnostic.strip()}"
        )

    sample_values = re.findall(r"n_samples:\s*(\d+)", diagnostic)
    mean_values = re.findall(r"mean_volume:\s*(-?inf|-?\d+(?:\.\d+)?)\s*dB", diagnostic)
    max_values = re.findall(r"max_volume:\s*(-?inf|-?\d+(?:\.\d+)?)\s*dB", diagnostic)
    if not sample_values or not mean_values or not max_values:
        raise MediaProcessError("FFmpeg did not return decoded audio level statistics")

    sample_count = int(sample_values[-1])
    try:
        mean_volume = float(mean_values[-1])
        max_volume = float(max_values[-1])
    except ValueError as error:
        raise MediaProcessError("Rendered audio is digitally silent") from error
    if sample_count <= 0 or not math.isfinite(mean_volume) or not math.isfinite(max_volume):
        raise MediaProcessError("Rendered audio is digitally silent")
    return sample_count, mean_volume, max_volume


def _positive_float(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise MediaProcessError(f"{label} is missing or invalid") from error
    if number <= 0:
        raise MediaProcessError(f"{label} must be positive")
    return number


def _parse_fps(value: Any) -> float:
    try:
        rate = float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError) as error:
        raise MediaProcessError(f"Invalid frame rate: {value}") from error
    if rate <= 0:
        raise MediaProcessError("Frame rate must be positive")
    return rate


def has_faststart(path: Path) -> bool:
    file_size = path.stat().st_size
    offset = 0
    moov_offset: int | None = None
    mdat_offset: int | None = None
    with path.open("rb") as source:
        while offset + 8 <= file_size:
            source.seek(offset)
            header = source.read(16)
            if len(header) < 8:
                break
            atom_size = int.from_bytes(header[0:4], "big")
            atom_type = header[4:8]
            header_size = 8
            if atom_size == 1:
                if len(header) < 16:
                    raise MediaProcessError("MP4 has a truncated extended atom header")
                atom_size = int.from_bytes(header[8:16], "big")
                header_size = 16
            elif atom_size == 0:
                atom_size = file_size - offset
            if atom_size < header_size or offset + atom_size > file_size:
                raise MediaProcessError("MP4 has an invalid top-level atom")
            if atom_type == b"moov":
                moov_offset = offset
            elif atom_type == b"mdat":
                mdat_offset = offset
            offset += atom_size
    return moov_offset is not None and mdat_offset is not None and moov_offset < mdat_offset


def validate_mp4(
    *,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str,
    video_path: Path,
    audio_path: Path,
    profile: RenderProfile | None = None,
    expected_audio_codec: str = "aac",
    expected_audio_sample_rate: int | None = None,
    expected_duration_seconds: float | None = None,
) -> VideoValidationResult:
    expected = profile or RenderProfile()
    if not video_path.is_file() or video_path.stat().st_size <= 0:
        raise MediaProcessError(f"Rendered MP4 is missing or empty: {video_path}")
    if not audio_path.is_file() or audio_path.stat().st_size <= 0:
        raise MediaProcessError(f"Source audio is missing or empty: {audio_path}")

    video_probe = _probe_json(ffprobe_path, video_path)
    audio_probe = _probe_json(ffprobe_path, audio_path)
    streams = video_probe.get("streams")
    if not isinstance(streams, list):
        raise MediaProcessError("Rendered MP4 has no streams array")
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if len(video_streams) != 1 or len(audio_streams) != 1:
        raise MediaProcessError("Rendered MP4 must contain exactly one video and one audio stream")
    video = video_streams[0]
    audio = audio_streams[0]
    if video.get("codec_name") != "h264":
        raise MediaProcessError("Rendered video codec must be H.264")
    if audio.get("codec_name") != expected_audio_codec:
        raise MediaProcessError(f"Rendered audio codec must be {expected_audio_codec}")
    if int(audio.get("channels", 0)) != 2:
        raise MediaProcessError("Rendered audio must contain two stereo channels")
    required_sample_rate = expected_audio_sample_rate or expected.audio_sample_rate
    if int(audio.get("sample_rate", 0)) != required_sample_rate:
        raise MediaProcessError(
            f"Rendered audio sample rate must be {required_sample_rate} Hz"
        )
    audio_sample_count, audio_mean_volume, audio_max_volume = _decoded_audio_levels(
        ffmpeg_path, video_path
    )
    if audio_max_volume < expected.minimum_audio_peak_db:
        raise MediaProcessError(
            f"Rendered audio peak {audio_max_volume:.1f} dB is below the required "
            f"{expected.minimum_audio_peak_db:.1f} dB"
        )
    if (
        int(video.get("width", 0)) != expected.width
        or int(video.get("height", 0)) != expected.height
    ):
        raise MediaProcessError(f"Rendered resolution must be {expected.width}x{expected.height}")
    if video.get("pix_fmt") != "yuv420p":
        raise MediaProcessError("Rendered pixel format must be yuv420p")
    fps = _parse_fps(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    if abs(fps - expected.fps) > 0.01:
        raise MediaProcessError(f"Rendered frame rate must be {expected.fps}, received {fps}")

    video_format = video_probe.get("format") or {}
    audio_format = audio_probe.get("format") or {}
    video_duration = _positive_float(video_format.get("duration"), "video duration")
    source_audio_duration = _positive_float(
        audio_format.get("duration"), "source audio duration"
    )
    rendered_audio_duration = _positive_float(
        audio.get("duration"), "rendered audio duration"
    )
    reference_duration = expected_duration_seconds or source_audio_duration
    duration_delta = abs(video_duration - reference_duration)
    if duration_delta > expected.max_duration_delta_seconds:
        raise MediaProcessError(
            f"Video/audio duration delta {duration_delta:.3f}s exceeds "
            f"{expected.max_duration_delta_seconds:.3f}s"
        )
    file_size = video_path.stat().st_size
    probed_size = int(video_format.get("size", 0))
    if probed_size != file_size:
        raise MediaProcessError(
            f"FFprobe size {probed_size} differs from filesystem size {file_size}"
        )
    faststart = has_faststart(video_path)
    if not faststart:
        raise MediaProcessError("Rendered MP4 must place moov before mdat for faststart")

    return VideoValidationResult(
        width=int(video["width"]),
        height=int(video["height"]),
        fps=fps,
        video_codec=str(video["codec_name"]),
        audio_codec=str(audio["codec_name"]),
        audio_channels=int(audio["channels"]),
        audio_sample_rate_hz=int(audio["sample_rate"]),
        audio_sample_count=audio_sample_count,
        audio_mean_volume_db=audio_mean_volume,
        audio_max_volume_db=audio_max_volume,
        pixel_format=str(video["pix_fmt"]),
        video_duration_seconds=video_duration,
        audio_duration_seconds=rendered_audio_duration,
        duration_delta_seconds=duration_delta,
        video_stream_count=len(video_streams),
        audio_stream_count=len(audio_streams),
        file_size_bytes=file_size,
        faststart=faststart,
        video_sha256=sha256_file(video_path),
    )


class FFmpegVideoRenderer:
    def __init__(
        self,
        *,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        font_dir: Path,
        profile: RenderProfile | None = None,
        intro_title: str = "Annas Brief",
    ) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.font_dir = font_dir
        self.profile = profile or RenderProfile()
        self._intro_title = intro_title

    def _render_sync(
        self,
        *,
        background: Path,
        audio: Path,
        subtitles: Path,
        destination: Path,
        intro_topic: str = "",
    ) -> Path:
        for source, label in (
            (background, "background"),
            (audio, "audio"),
            (subtitles, "subtitles"),
        ):
            if not source.is_file() or source.stat().st_size <= 0:
                raise FileNotFoundError(f"{label} is missing or empty: {source}")
        if not self.font_dir.is_dir():
            raise FileNotFoundError(f"font directory is missing: {self.font_dir}")
        # Dancing Script has no Cyrillic glyphs; use the bundled Noto Sans.
        title_font_name = (
            _INTRO_TOPIC_FONT
            if re.search(r"[Ѐ-ԯ]", self._intro_title)
            else _INTRO_TITLE_FONT
        )
        title_font = self.font_dir / title_font_name
        topic_font = self.font_dir / _INTRO_TOPIC_FONT
        for font, label in ((title_font, "intro title font"), (topic_font, "intro topic font")):
            if not font.is_file() or font.stat().st_size <= 0:
                raise FileNotFoundError(f"{label} is missing or empty: {font}")
        audio_probe = _probe_json(self.ffprobe_path, audio)
        audio_duration = _positive_float(
            (audio_probe.get("format") or {}).get("duration"), "audio duration"
        )
        output_duration = self.profile.duration_seconds or audio_duration
        audio_filter_args: list[str] = []
        if self.profile.duration_seconds > 0:
            audio_filter_args = [
                "-af",
                f"apad=pad_dur={output_duration:.6f},atrim=duration={output_duration:.6f}",
            ]

        destination.parent.mkdir(parents=True, exist_ok=True)
        working_directory = destination.parent.resolve()
        subtitle_name = subtitles.resolve().name
        if subtitles.resolve().parent != working_directory:
            raise ValueError("ASS file and output MP4 must share the same work directory")
        if not subtitle_name.replace("_", "").replace("-", "").replace(".", "").isalnum():
            raise ValueError("ASS filename contains unsupported characters")
        relative_font_dir = os.path.relpath(self.font_dir.resolve(), working_directory).replace(
            os.sep, "/"
        )
        title_font_path = _escape_drawtext_path(
            os.path.relpath(title_font.resolve(), working_directory)
        )
        topic_font_path = _escape_drawtext_path(
            os.path.relpath(topic_font.resolve(), working_directory)
        )
        topic_text, topic_font_size = _layout_intro_topic(intro_topic)
        video_filters = [
            f"scale={self.profile.width}:{self.profile.height}:"
            "force_original_aspect_ratio=increase",
            f"crop={self.profile.width}:{self.profile.height}",
            (
                f"drawtext=fontfile='{title_font_path}':"
                f"text='{_escape_drawtext_text(self._intro_title)}':"
                f"x={_INTRO_X}:y={_INTRO_TITLE_Y}:"
                f"fontsize={_INTRO_TITLE_FONT_SIZE}:fontcolor={_INTRO_TITLE_FONT_COLOR}:"
                f"{_INTRO_SHADOW}:alpha='{_INTRO_FADE_ALPHA}'"
            ),
            (
                f"drawtext=fontfile='{topic_font_path}':"
                f"text='{_escape_drawtext_text(topic_text)}':x={_INTRO_X}:y={_INTRO_TOPIC_Y}:"
                f"fontsize={topic_font_size}:fontcolor={_INTRO_TOPIC_FONT_COLOR}:"
                f"line_spacing={_INTRO_TOPIC_LINE_SPACING}:{_INTRO_SHADOW}:"
                f"alpha='{_INTRO_FADE_ALPHA}'"
            ),
            f"ass={subtitle_name}:fontsdir={relative_font_dir}",
        ]
        subtitle_filter = ",".join(video_filters)
        command = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-loop",
            "1",
            "-framerate",
            str(self.profile.fps),
            "-i",
            str(background.resolve()),
            "-i",
            str(audio.resolve()),
            "-vf",
            subtitle_filter,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map_metadata",
            "-1",
            *audio_filter_args,
            "-c:v",
            self.profile.video_codec,
            "-preset",
            self.profile.video_preset,
            "-crf",
            str(self.profile.video_crf),
            "-tune",
            "stillimage",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(self.profile.fps),
            "-c:a",
            "aac",
            "-profile:a",
            "aac_low",
            "-b:a",
            self.profile.audio_bitrate,
            "-ar",
            str(self.profile.audio_sample_rate),
            "-ac",
            "2",
            "-disposition:a:0",
            "default",
            "-t",
            f"{output_duration:.6f}",
            "-movflags",
            "+faststart",
            destination.name,
        ]
        _run(command, cwd=working_directory)
        return destination

    async def render(
        self,
        *,
        background: Path,
        audio: Path,
        subtitles: Path,
        destination: Path,
        intro_topic: str = "",
    ) -> Path:
        return await asyncio.to_thread(
            self._render_sync,
            background=background,
            audio=audio,
            subtitles=subtitles,
            destination=destination,
            intro_topic=intro_topic,
        )

    async def validate(
        self,
        video_path: Path,
        *,
        audio_path: Path,
        expected_audio_codec: str = "aac",
        expected_audio_sample_rate: int | None = None,
    ) -> VideoValidationResult:
        return await asyncio.to_thread(
            validate_mp4,
            ffmpeg_path=self.ffmpeg_path,
            ffprobe_path=self.ffprobe_path,
            video_path=video_path,
            audio_path=audio_path,
            profile=self.profile,
            expected_audio_codec=expected_audio_codec,
            expected_audio_sample_rate=expected_audio_sample_rate,
            expected_duration_seconds=self.profile.duration_seconds or None,
        )
