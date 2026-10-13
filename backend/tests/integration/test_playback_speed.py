"""Local FFmpeg verification; no provider calls."""

import asyncio
import math
import struct
import wave

import pytest

from anna_backend.audio.learning import (
    FFmpegLearningAudioPacer,
    FFmpegReadyAudioEncoder,
    LearningPace,
)
from anna_backend.cli.make_video import DEFAULT_FONT_DIR
from anna_backend.media_types import AlignmentResult, WordTiming
from anna_backend.subtitles.ass import AssSubtitleRenderer
from anna_backend.video.ffmpeg import FFmpegVideoRenderer, RenderProfile


@pytest.mark.parametrize("rate", (0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20))
def test_saved_video_audio_and_word_timings_follow_selected_speed(tmp_path, rate):
    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(
            b"".join(
                struct.pack(
                    "<h",
                    int(
                        12000
                        * (0.1 + 0.9 * (0.5 + 0.5 * math.sin(2 * math.pi * 3 * i / 48000)) ** 4)
                        * math.sin(2 * math.pi * 440 * i / 48000)
                    ),
                )
                for i in range(96000)
            )
        )
    background = tmp_path / "background.ppm"
    background.write_bytes(b"P6\n2 2\n255\n" + bytes([60, 60, 60]) * 4)
    alignment = AlignmentResult(
        words=(
            WordTiming("Hello", 0.4, 0.8),
            WordTiming("world.", 1.0, 1.8),
        ),
        score=100.0,
    )
    paced = tmp_path / "paced.wav"
    mp3 = tmp_path / "ready.mp3"
    ass = tmp_path / "karaoke.ass"
    video = tmp_path / "ready.mp4"

    async def generate():
        timed = await FFmpegLearningAudioPacer(pace=LearningPace(playback_rate=rate)).pace(
            source=source,
            alignment=alignment,
            destination=paced,
        )
        for original, changed in zip(alignment.words, timed.words, strict=True):
            assert changed.start_seconds == pytest.approx(0.2 + original.start_seconds / rate)
            assert changed.end_seconds == pytest.approx(0.2 + original.end_seconds / rate)
        await AssSubtitleRenderer().render(timed, ass)
        await FFmpegReadyAudioEncoder().encode(source=paced, destination=mp3)
        renderer = FFmpegVideoRenderer(
            font_dir=DEFAULT_FONT_DIR,
            profile=RenderProfile(width=320, height=568, video_preset="ultrafast"),
        )
        await renderer.render(
            background=background,
            audio=paced,
            subtitles=ass,
            destination=video,
            intro_topic="Speed test",
        )
        result = await renderer.validate(video, audio_path=mp3)
        assert result.video_duration_seconds == pytest.approx(0.2 + 2.0 / rate, abs=0.12)
        assert result.audio_duration_seconds == pytest.approx(0.2 + 2.0 / rate, abs=0.12)
        assert result.audio_sample_count > 0

    asyncio.run(generate())
