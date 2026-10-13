import pytest

from anna_backend.alignment.aligner import AlignmentError, align_spoken_text
from anna_backend.cli import make_video
from anna_backend.local_pipeline import _normalize_spoken_text, _publish_pair
from anna_backend.media_types import WordTiming


@pytest.mark.parametrize("existing", [True, False])
def test_video_publication_failure_restores_audio(tmp_path, monkeypatch, existing):
    audio, video = tmp_path / "ready.mp3", tmp_path / "ready.mp4"
    audio_candidate, video_candidate = tmp_path / "new.mp3", tmp_path / "new.mp4"
    audio_candidate.write_bytes(b"new audio")
    video_candidate.write_bytes(b"new video")
    if existing:
        audio.write_bytes(b"old audio")
        video.write_bytes(b"old video")
    import os

    original = os.replace

    def fail_video(source, destination):
        if destination == video:
            raise PermissionError("video locked")
        original(source, destination)

    monkeypatch.setattr(os, "replace", fail_video)
    with pytest.raises(PermissionError, match="video locked"):
        _publish_pair(audio_candidate, audio, video_candidate, video)
    if existing:
        assert audio.read_bytes() == b"old audio"
        assert video.read_bytes() == b"old video"
    else:
        assert not audio.exists()
        assert not video.exists()
    assert not list(tmp_path.glob(".audio-rollback-*"))


def test_successful_pair_publication(tmp_path):
    audio, video = tmp_path / "ready.mp3", tmp_path / "ready.mp4"
    audio_candidate, video_candidate = tmp_path / "new.mp3", tmp_path / "new.mp4"
    audio.write_bytes(b"old audio")
    video.write_bytes(b"old video")
    audio_candidate.write_bytes(b"new audio")
    video_candidate.write_bytes(b"new video")
    _publish_pair(audio_candidate, audio, video_candidate, video)
    assert audio.read_bytes() == b"new audio"
    assert video.read_bytes() == b"new video"
    assert not list(tmp_path.glob(".audio-rollback-*"))


@pytest.mark.parametrize("gap", [0.0, 0.005])
def test_missing_word_cannot_overlap_next_word(gap):
    target = [f"word{i}" for i in range(100)]
    heard = [word for i, word in enumerate(target) if i != 50]
    transcript = tuple(
        WordTiming(word, i * 0.1 + (gap if i >= 50 else 0), (i + 1) * 0.1 + (gap if i >= 50 else 0))
        for i, word in enumerate(heard)
    )
    with pytest.raises(AlignmentError, match="time interval"):
        align_spoken_text(" ".join(target), transcript)


def test_meaningful_symbols_survive_normalization():
    text = "Температура −5 °C. Ціна 5 € та 50 %. 2 + 2 = 4."
    assert _normalize_spoken_text(text) == text


@pytest.mark.parametrize("failure", ["missing_file", "missing_key"])
def test_cli_preparation_errors_have_traceback(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(make_video, "DEFAULT_TMP_DIR", tmp_path / "tmp")

    class Settings:
        openai_api_key = None

    monkeypatch.setattr(make_video, "_CliSettings", Settings)
    args = [
        "--topic",
        "Test",
        "--background",
        str(tmp_path / "background.png"),
        "--output-name",
        "test",
    ]
    if failure == "missing_file":
        args += ["--text-file", str(tmp_path / "missing.txt")]
    else:
        args += ["--text", "Approved text."]
    assert make_video.main(args) == 2
    logs = list((tmp_path / "logs").glob("*.log"))
    assert len(logs) == 1
    report = logs[0].read_text(encoding="utf-8")
    assert "FAIL CLI preparation and generation" in report
    assert "Traceback (most recent call last)" in report
    assert (
        "Could not read UTF-8" if failure == "missing_file" else "OPENAI_API_KEY is required"
    ) in report
