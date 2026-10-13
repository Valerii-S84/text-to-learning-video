from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from anna_backend.cli import make_video as make_video_module
from anna_backend.media_types import LocalMediaResult


def _result(tmp_path: Path) -> LocalMediaResult:
    return LocalMediaResult(
        audio_path=tmp_path / "ready-audio" / "B005.mp3",
        video_path=tmp_path / "ready-videos" / "B005.mp4",
        alignment_score=99.4,
        whisper_strategy="verbose_json",
        video_sha256="abc123",
    )


def _install_composition_fakes(
    monkeypatch: pytest.MonkeyPatch,
    result: LocalMediaResult,
) -> SimpleNamespace:
    text_to_speech = object()
    transcriber = object()
    audio_pacer = object()
    ready_audio_encoder = object()
    subtitle_renderer = object()
    video_renderer = object()
    pipeline_run = AsyncMock(return_value=result)
    pipeline = SimpleNamespace(run=pipeline_run)
    text_to_speech_factory = Mock(return_value=text_to_speech)
    transcriber_factory = Mock(return_value=transcriber)
    audio_pacer_factory = Mock(return_value=audio_pacer)
    ready_audio_encoder_factory = Mock(return_value=ready_audio_encoder)
    subtitle_renderer_factory = Mock(return_value=subtitle_renderer)
    video_renderer_factory = Mock(return_value=video_renderer)
    pipeline_factory = Mock(return_value=pipeline)
    monkeypatch.setattr(
        make_video_module, "OpenAIAudioGenerator", text_to_speech_factory
    )
    monkeypatch.setattr(
        make_video_module, "OpenAIWhisperWordTranscriber", transcriber_factory
    )
    monkeypatch.setattr(
        make_video_module, "FFmpegLearningAudioPacer", audio_pacer_factory
    )
    monkeypatch.setattr(
        make_video_module, "FFmpegReadyAudioEncoder", ready_audio_encoder_factory
    )
    monkeypatch.setattr(
        make_video_module, "AssSubtitleRenderer", subtitle_renderer_factory
    )
    monkeypatch.setattr(
        make_video_module, "FFmpegVideoRenderer", video_renderer_factory
    )
    monkeypatch.setattr(make_video_module, "LocalPipeline", pipeline_factory)
    return SimpleNamespace(
        text_to_speech=text_to_speech,
        transcriber=transcriber,
        audio_pacer=audio_pacer,
        ready_audio_encoder=ready_audio_encoder,
        subtitle_renderer=subtitle_renderer,
        video_renderer=video_renderer,
        pipeline_run=pipeline_run,
        text_to_speech_factory=text_to_speech_factory,
        transcriber_factory=transcriber_factory,
        audio_pacer_factory=audio_pacer_factory,
        ready_audio_encoder_factory=ready_audio_encoder_factory,
        subtitle_renderer_factory=subtitle_renderer_factory,
        video_renderer_factory=video_renderer_factory,
        pipeline_factory=pipeline_factory,
    )


def test_main_runs_pipeline_with_direct_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = _result(tmp_path)
    composition = _install_composition_fakes(monkeypatch, result)
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")

    exit_code = make_video_module.main(
        [
            "--text",
            "Hallo Anna.",
            "--topic",
            "Begrüßung",
            "--background",
            str(tmp_path / "background.png"),
            "--output-name",
            "B005",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    composition.text_to_speech_factory.assert_called_once_with(
        api_key="test-api-key",
        profile=make_video_module.ANNA_MARIN_PROFILE,
    )
    composition.transcriber_factory.assert_called_once_with(
        api_key="test-api-key",
        language="de",
    )
    composition.audio_pacer_factory.assert_called_once_with()
    composition.ready_audio_encoder_factory.assert_called_once_with()
    composition.subtitle_renderer_factory.assert_called_once_with()
    composition.video_renderer_factory.assert_called_once_with(
        font_dir=make_video_module.DEFAULT_FONT_DIR
    )
    composition.pipeline_factory.assert_called_once_with(
        text_to_speech=composition.text_to_speech,
        transcriber=composition.transcriber,
        audio_pacer=composition.audio_pacer,
        ready_audio_encoder=composition.ready_audio_encoder,
        subtitle_renderer=composition.subtitle_renderer,
        video_renderer=composition.video_renderer,
    )
    composition.pipeline_run.assert_awaited_once_with(
        text="Hallo Anna.",
        topic="Begrüßung",
        background=tmp_path / "background.png",
        output_name="B005",
        ready_audio_dir=make_video_module.DEFAULT_READY_AUDIO_DIR,
        ready_video_dir=make_video_module.DEFAULT_READY_VIDEO_DIR,
        tmp_dir=make_video_module.DEFAULT_TMP_DIR,
    )
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "audio_path": str(result.audio_path),
        "video_path": str(result.video_path),
        "alignment_score": 99.4,
        "whisper_strategy": "verbose_json",
        "video_sha256": "abc123",
    }


def test_result_json_uses_ascii_fallback_for_incompatible_console_encoding(
    tmp_path: Path,
) -> None:
    result = _result(tmp_path / "🍃")

    payload = make_video_module._result_json(result, output_encoding="cp1252")

    assert json.loads(payload)["audio_path"] == str(result.audio_path)
    assert "\\ud83c\\udf43" in payload


@pytest.mark.parametrize("language", ("de", "en", "uk", "pl", "nl"))
def test_main_passes_supported_language_and_custom_instructions_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    language: str,
) -> None:
    composition = _install_composition_fakes(monkeypatch, _result(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
    instructions = f"Exact {language} speaking instructions."

    exit_code = make_video_module.main(
        [
            "--text",
            "Approved text.",
            "--topic",
            "Topic",
            "--background",
            str(tmp_path / "background.png"),
            "--output-name",
            "language-test",
            "--language",
            language,
            "--instructions",
            instructions,
        ]
    )

    assert exit_code == 0
    profile = composition.text_to_speech_factory.call_args.kwargs["profile"]
    assert profile.instructions == instructions
    composition.transcriber_factory.assert_called_once_with(
        api_key="test-api-key",
        language=language,
    )


def test_main_rejects_unknown_language_with_argparse(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        make_video_module.main(
            [
                "--text",
                "Approved text.",
                "--topic",
                "Topic",
                "--background",
                str(tmp_path / "background.png"),
                "--output-name",
                "language-test",
                "--language",
                "xx",
            ]
        )

    assert exc_info.value.code == 2


def test_non_german_without_instructions_stops_before_tts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    composition = _install_composition_fakes(monkeypatch, _result(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")

    exit_code = make_video_module.main(
        [
            "--text",
            "Сьогодні гарний день.",
            "--topic",
            "Тема",
            "--background",
            str(tmp_path / "background.png"),
            "--output-name",
            "language-test",
            "--language",
            "uk",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "--instructions is required" in captured.err
    composition.text_to_speech_factory.assert_not_called()
    composition.transcriber_factory.assert_not_called()
    composition.pipeline_factory.assert_not_called()


def test_main_reads_utf8_text_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    approved_text = "Grüße, Anna! 🌷\n"
    text_file = tmp_path / "episode.txt"
    text_file.write_text(approved_text, encoding="utf-8")
    composition = _install_composition_fakes(monkeypatch, _result(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")

    exit_code = make_video_module.main(
        [
            "--text-file",
            str(text_file),
            "--topic",
            "Grüße",
            "--background",
            str(tmp_path / "background.png"),
            "--output-name",
            "B006",
        ]
    )

    assert exit_code == 0
    composition.pipeline_run.assert_awaited_once_with(
        text="Grüße, Anna! 🌷",
        topic="Grüße",
        background=tmp_path / "background.png",
        output_name="B006",
        ready_audio_dir=make_video_module.DEFAULT_READY_AUDIO_DIR,
        ready_video_dir=make_video_module.DEFAULT_READY_VIDEO_DIR,
        tmp_dir=make_video_module.DEFAULT_TMP_DIR,
    )


def test_main_rejects_empty_file_before_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    text_file = tmp_path / "empty.txt"
    text_file.write_text(" \n\t", encoding="utf-8")
    composition = _install_composition_fakes(monkeypatch, _result(tmp_path))
    settings_factory = Mock()
    monkeypatch.setattr(make_video_module, "_CliSettings", settings_factory)

    exit_code = make_video_module.main(
        [
            "--text-file",
            str(text_file),
            "--topic",
            "Leerer Text",
            "--background",
            str(tmp_path / "background.png"),
            "--output-name",
            "B007",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "make_video: error: Text must not be empty.\n"
    settings_factory.assert_not_called()
    composition.text_to_speech_factory.assert_not_called()
    composition.transcriber_factory.assert_not_called()
    composition.video_renderer_factory.assert_not_called()
    composition.pipeline_factory.assert_not_called()
    composition.pipeline_run.assert_not_awaited()


def test_main_reports_missing_api_key_without_running_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    composition = _install_composition_fakes(monkeypatch, _result(tmp_path))
    monkeypatch.setattr(
        make_video_module,
        "_CliSettings",
        Mock(return_value=SimpleNamespace(openai_api_key=None)),
    )

    exit_code = make_video_module.main(
        [
            "--text",
            "Hallo Anna.",
            "--topic",
            "API-Key",
            "--background",
            str(tmp_path / "background.png"),
            "--output-name",
            "B008",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "OPENAI_API_KEY is required" in captured.err
    assert "test-api-key" not in captured.err
    composition.text_to_speech_factory.assert_not_called()
    composition.transcriber_factory.assert_not_called()
    composition.video_renderer_factory.assert_not_called()
    composition.pipeline_factory.assert_not_called()
    composition.pipeline_run.assert_not_awaited()
