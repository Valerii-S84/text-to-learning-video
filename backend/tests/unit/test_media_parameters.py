from pathlib import Path

import pytest

import anna_backend.video.ffmpeg as video_module
from anna_backend.video import FFmpegVideoRenderer, RenderProfile


@pytest.mark.parametrize(
    ("renderer_kwargs", "expected_intro_title"),
    [
        ({}, "Annas Brief"),
        ({"intro_title": "Abends Brief"}, "Abends Brief"),
    ],
)
def test_render_command_uses_requested_intro_title_and_exact_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    renderer_kwargs: dict[str, str],
    expected_intro_title: str,
) -> None:
    background = tmp_path / "render-input.png"
    audio = tmp_path / "audio.wav"
    subtitles = tmp_path / "lesson.ass"
    destination = tmp_path / "lesson.mp4"
    font_dir = tmp_path / "fonts"
    background.write_bytes(b"image")
    audio.write_bytes(b"audio")
    subtitles.write_text("subtitles", encoding="utf-8")
    font_dir.mkdir()
    (font_dir / "DancingScript-SemiBold.ttf").write_bytes(b"title-font")
    (font_dir / "NotoSans-SemiBold.ttf").write_bytes(b"topic-font")
    captured: list[str] = []

    monkeypatch.setattr(
        video_module,
        "_probe_json",
        lambda *args: {"format": {"duration": "50.0"}},
    )

    def fake_run(command: list[str], *, cwd: Path | None = None) -> None:
        captured.extend(command)
        destination.write_bytes(b"video")

    monkeypatch.setattr(video_module, "_run", fake_run)
    renderer = FFmpegVideoRenderer(
        font_dir=font_dir,
        profile=RenderProfile(duration_seconds=42.0),
        **renderer_kwargs,
    )

    assert renderer._render_sync(
        background=background,
        audio=audio,
        subtitles=subtitles,
        destination=destination,
        intro_topic="Neue Wege für Anna heute",
    ) == destination
    assert captured[captured.index("-t") + 1] == "42.000000"
    audio_filter = captured[captured.index("-af") + 1]
    assert "apad=pad_dur=42.000000" in audio_filter
    assert "atrim=duration=42.000000" in audio_filter
    video_filter = captured[captured.index("-vf") + 1]
    assert video_filter.count("drawtext=") == 2
    assert f"text='{expected_intro_title}'" in video_filter
    assert video_filter.rsplit(",", maxsplit=1)[-1].startswith("ass=lesson.ass:")


def test_long_intro_topic_wraps_and_uses_42_pixel_fallback() -> None:
    topic = (
        "Ein außergewöhnlich langer Nachmittag mit vielen überraschenden Begegnungen "
        "und mehreren neuen Aufgaben"
    )

    rendered, font_size = video_module._layout_intro_topic(topic)

    assert rendered == (
        "Ein außergewöhnlich langer\n"
        "Nachmittag mit vielen überraschenden Begegnungen und mehreren neuen Aufgaben"
    )
    assert font_size == 42
