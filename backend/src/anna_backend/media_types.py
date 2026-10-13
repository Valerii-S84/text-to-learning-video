from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WordTiming:
    word: str
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True, slots=True)
class AlignmentResult:
    words: tuple[WordTiming, ...]
    score: float


@dataclass(frozen=True, slots=True)
class VideoValidationResult:
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str
    audio_channels: int
    audio_sample_rate_hz: int
    audio_sample_count: int
    audio_mean_volume_db: float
    audio_max_volume_db: float
    pixel_format: str
    video_duration_seconds: float
    audio_duration_seconds: float
    duration_delta_seconds: float
    video_stream_count: int
    audio_stream_count: int
    file_size_bytes: int
    faststart: bool
    video_sha256: str

@dataclass(frozen=True, slots=True)
class LocalMediaResult:
    audio_path: Path
    video_path: Path
    alignment_score: float
    whisper_strategy: str
    video_sha256: str
