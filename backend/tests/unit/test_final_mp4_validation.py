from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from anna_backend.video import ffmpeg as ffmpeg_module
from anna_backend.video.ffmpeg import MediaProcessError, validate_mp4


def _stub_probes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    file_size: int,
    rendered_audio_duration: str,
) -> None:
    video_probe = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1080,
                "height": 1920,
                "pix_fmt": "yuv420p",
                "avg_frame_rate": "30/1",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "sample_rate": "48000",
                "duration": rendered_audio_duration,
            },
        ],
        "format": {"duration": "1.0", "size": str(file_size)},
    }
    source_probe = {"format": {"duration": "1.0"}}
    probes = iter((video_probe, source_probe))
    monkeypatch.setattr(ffmpeg_module, "_probe_json", lambda *_: next(probes))
    monkeypatch.setattr(ffmpeg_module, "has_faststart", lambda _: True)


def _media_paths(tmp_path: Path) -> tuple[Path, Path]:
    video_path = tmp_path / "final.mp4"
    source_path = tmp_path / "source.wav"
    video_path.write_bytes(b"video")
    source_path.write_bytes(b"audio")
    return video_path, source_path


def test_final_mp4_validation_rejects_zero_audio_stream_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video_path, source_path = _media_paths(tmp_path)
    _stub_probes(
        monkeypatch,
        file_size=video_path.stat().st_size,
        rendered_audio_duration="0.0",
    )
    monkeypatch.setattr(
        ffmpeg_module, "_decoded_audio_levels", lambda *_: (1024, -20.0, -1.0)
    )

    with pytest.raises(MediaProcessError, match="rendered audio duration must be positive"):
        validate_mp4(
            ffprobe_path="ffprobe",
            video_path=video_path,
            audio_path=source_path,
        )


@pytest.mark.parametrize(
    ("rms_db", "peak_db"),
    (("-inf", "-1.0"), ("-20.0", "-inf")),
    ids=("rms-is-negative-infinity", "peak-is-negative-infinity"),
)
def test_final_mp4_validation_rejects_silent_signal_levels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rms_db: str,
    peak_db: str,
) -> None:
    video_path, source_path = _media_paths(tmp_path)
    _stub_probes(
        monkeypatch,
        file_size=video_path.stat().st_size,
        rendered_audio_duration="1.0",
    )
    diagnostic = (
        "n_samples: 1024\n"
        f"mean_volume: {rms_db} dB\n"
        f"max_volume: {peak_db} dB\n"
    )
    monkeypatch.setattr(
        ffmpeg_module.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=diagnostic
        ),
    )

    with pytest.raises(MediaProcessError, match="digitally silent"):
        validate_mp4(
            ffprobe_path="ffprobe",
            video_path=video_path,
            audio_path=source_path,
        )
