from __future__ import annotations

import os
import threading
from importlib import import_module
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, Qt, QThread, QTimer, QUrl
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication

import anna_backend.admin_app as admin_module
from anna_backend.audio.learning import LearningPace
from anna_backend.media_types import LocalMediaResult
from anna_backend.tts.profile import SpeechProfile


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_saved_backgrounds(monkeypatch, tmp_path):
    monkeypatch.setattr(admin_module, "SAVED_BACKGROUNDS_DIR", tmp_path / "saved-backgrounds")


def test_language_selector_has_five_options_and_defaults_to_german(
    qt_app: QApplication,
) -> None:
    del qt_app
    window = admin_module.AdminWindow()

    assert [
        (window.language_input.itemText(index), window.language_input.itemData(index))
        for index in range(window.language_input.count())
    ] == list(admin_module.LANGUAGE_OPTIONS)
    assert window.language_input.currentText() == "German"
    assert window.language_input.currentData() == "de"
    assert window.instructions_input.toPlainText() == admin_module.ANNA_MARIN_PROFILE.instructions
    window.close()


def test_language_selection_applies_ukrainian_default_and_keeps_instructions_editable(
    qt_app: QApplication,
) -> None:
    del qt_app
    window = admin_module.AdminWindow()

    window.language_input.setCurrentIndex(window.language_input.findData("uk"))

    assert (
        window.instructions_input.toPlainText()
        == admin_module.LANGUAGE_INSTRUCTIONS["uk"]
    )

    window.instructions_input.setPlainText("Мої власні інструкції")
    assert window.instructions_input.toPlainText() == "Мої власні інструкції"
    window.close()


@pytest.mark.parametrize("role", ["educational", "humorous", "news"])
@pytest.mark.parametrize("language_name,language", admin_module.LANGUAGE_OPTIONS)
def test_speaking_roles_follow_language_without_changing_voice_or_text(
    qt_app: QApplication, role: str, language_name: str, language: str,
) -> None:
    del qt_app
    window = admin_module.AdminWindow()
    try:
        approved_text = "Approved text stays unchanged."
        window.text_input.setPlainText(approved_text)
        window.voice_input.setCurrentIndex(window.voice_input.findData("onyx"))
        window.speed_input.setCurrentIndex(window.speed_input.findData(1.10))
        window.role_input.setCurrentIndex(window.role_input.findData(role))
        window.language_input.setCurrentIndex(window.language_input.findData(language))

        instructions = window.instructions_input.toPlainText()
        assert instructions.startswith(f"Мова озвучення: {language_name} ({language}).")
        assert admin_module.SPEAKING_ROLES[role][1] in instructions
        assert "не перекладай, не перефразовуй" in instructions
        assert window.role_input.currentData() == role
        assert window.voice_input.currentData() == "onyx"
        assert window.speed_input.currentData() == 1.10
        assert window.text_input.toPlainText() == approved_text

        window.instructions_input.setPlainText("Власна манера озвучення")
        assert window.instructions_input.toPlainText() == "Власна манера озвучення"
        window.role_input.setCurrentIndex(window.role_input.findData("default"))
        assert window.instructions_input.toPlainText() == admin_module.LANGUAGE_INSTRUCTIONS.get(
            language, admin_module.ANNA_MARIN_PROFILE.instructions,
        )
    finally:
        window.close()


class _FakePipeline:
    def __init__(self, outcome: LocalMediaResult | Exception) -> None:
        self.outcome = outcome
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls: list[dict[str, object]] = []

    async def run(self, **values: object) -> LocalMediaResult:
        self.calls.append(values)
        self.started.set()
        if not self.release.wait(timeout=2.0):
            raise RuntimeError("fake pipeline was not released")
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _release_and_wait_for_thread(
    *,
    qt_app: QApplication,
    pipeline: _FakePipeline,
    thread: QThread,
) -> None:
    event_loop = QEventLoop()
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(event_loop.quit)
    stopped: list[bool] = []
    thread.finished.connect(lambda: stopped.append(not thread.isRunning()))
    thread.finished.connect(event_loop.quit)
    QTimer.singleShot(0, pipeline.release.set)
    timeout.start(2000)
    event_loop.exec()
    timeout.stop()
    assert stopped == [True]
    qt_app.processEvents()


def _filled_window(
    *,
    pipeline: _FakePipeline,
    monkeypatch: pytest.MonkeyPatch,
    topic: str,
) -> tuple[admin_module.AdminWindow, Mock]:
    window = admin_module.AdminWindow()
    window.title_input.setText("Annas Brief")
    window.topic_input.setText(topic)
    window.text_input.setPlainText("Wir kaufen heute frisches Brot.")
    window.voice_input.setCurrentIndex(window.voice_input.findData("marin"))
    window.instructions_input.setPlainText("Speak clearly")
    window.background_input.setText("C:/images/supermarket.png")
    pipeline_builder = Mock(return_value=pipeline)
    monkeypatch.setattr(window, "_build_pipeline", pipeline_builder)
    return window, pipeline_builder


def _media_result(tmp_path: Path, name: str) -> LocalMediaResult:
    return LocalMediaResult(
        audio_path=tmp_path / "ready-audio" / f"{name}.mp3",
        video_path=tmp_path / "ready-videos" / f"{name}.mp4",
        alignment_score=100.0,
        whisper_strategy="word",
        video_sha256=f"{name}-video-sha256",
    )


@pytest.mark.parametrize("sentence_count", [1, 10, 30])
def test_manual_text_normalization_accepts_arbitrary_sentence_counts(
    sentence_count: int,
) -> None:
    sentences = [f"Satz {index}." for index in range(1, sentence_count + 1)]
    pasted_text = "  \r\n\r\n\t".join(sentences)

    normalized = admin_module._normalize_manual_text(f" \n{pasted_text}\r\n ")

    assert normalized == " ".join(sentences)
    assert "\n" not in normalized
    assert "\r" not in normalized
    assert "\t" not in normalized


def test_whitespace_only_manual_text_remains_required(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pipeline = _FakePipeline(_media_result(tmp_path, "unused"))
    window, pipeline_builder = _filled_window(
        pipeline=pipeline,
        monkeypatch=monkeypatch,
        topic="Einkaufen im Supermarkt",
    )
    window.text_input.setPlainText(" \r\n\t \n")

    window.generate_button.click()

    assert window.status_label.text() == "Text is required"
    assert window._generation_thread is None
    pipeline_builder.assert_not_called()
    qt_app.processEvents()
    window.close()


def test_background_preview_starts_with_default_and_updates_on_selection(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    del qt_app
    window = admin_module.AdminWindow()
    default_pixmap = window.background_preview.pixmap()

    assert window.background_input.text() == str(admin_module.DEFAULT_BACKGROUND_IMAGE)
    assert not default_pixmap.isNull()

    selected_image = tmp_path / "selected-background.png"
    image = QImage(40, 20, QImage.Format.Format_RGB32)
    image.fill(QColor("magenta"))
    assert image.save(str(selected_image))
    monkeypatch.setattr(
        admin_module.QFileDialog,
        "getOpenFileName",
        Mock(return_value=(str(selected_image), "Images (*.png)")),
    )

    window.choose_image_button.click()

    selected_pixmap = window.background_preview.pixmap()
    expected_pixmap = QPixmap(str(selected_image)).scaled(
        window.background_preview.size(),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    saved_image = admin_module.SAVED_BACKGROUNDS_DIR / selected_image.name
    assert window.background_input.text() == str(saved_image)
    assert saved_image.read_bytes() == selected_image.read_bytes()
    assert not selected_pixmap.isNull()
    assert selected_pixmap.cacheKey() != default_pixmap.cacheKey()
    assert selected_pixmap.size() == expected_pixmap.size()
    window.close()


def test_video_preview_uses_comfortable_portrait_layout(
    qt_app: QApplication,
) -> None:
    del qt_app
    window = admin_module.AdminWindow()
    minimum_size = window.video_widget.minimumSize()
    main_layout = window._content_widget.layout()

    assert minimum_size.width() == 360
    assert minimum_size.height() == 640
    assert minimum_size.width() * 16 == minimum_size.height() * 9
    assert isinstance(main_layout, admin_module.QHBoxLayout)
    assert main_layout.count() == 2
    assert window.width() == 1100
    assert window.height() == 760
    window.close()


def test_preview_is_disabled_before_success_and_uses_success_paths(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    del qt_app
    window = admin_module.AdminWindow()
    first_result = _media_result(tmp_path, "first")

    assert not window.play_audio_button.isEnabled()
    assert not window.stop_audio_button.isEnabled()
    assert not window.play_video_button.isEnabled()
    assert not window.stop_video_button.isEnabled()
    assert window.audio_player.source().isEmpty()
    assert window.video_player.source().isEmpty()

    window._generation_succeeded(first_result)

    assert window.audio_player.source() == QUrl.fromLocalFile(
        str(first_result.audio_path)
    )
    assert window.video_player.source() == QUrl.fromLocalFile(
        str(first_result.video_path)
    )
    assert window.play_audio_button.isEnabled()
    assert window.stop_audio_button.isEnabled()
    assert window.play_video_button.isEnabled()
    assert window.stop_video_button.isEnabled()
    window.close()


def test_new_success_replaces_preview_and_failure_preserves_it(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    del qt_app
    window = admin_module.AdminWindow()
    first_result = _media_result(tmp_path, "first")
    second_result = _media_result(tmp_path, "second")
    window._generation_succeeded(first_result)

    window._generation_succeeded(second_result)

    expected_audio_source = QUrl.fromLocalFile(str(second_result.audio_path))
    expected_video_source = QUrl.fromLocalFile(str(second_result.video_path))
    assert window.last_media_result is second_result
    assert window.audio_player.source() == expected_audio_source
    assert window.video_player.source() == expected_video_source

    window._generation_failed("render failed")

    assert window.status_label.text() == "Error: render failed"
    assert window.last_media_result is second_result
    assert window.audio_player.source() == expected_audio_source
    assert window.video_player.source() == expected_video_source
    assert window.play_audio_button.isEnabled()
    assert window.stop_audio_button.isEnabled()
    assert window.play_video_button.isEnabled()
    assert window.stop_video_button.isEnabled()
    window.close()


@pytest.mark.parametrize("voice", ["marin", "onyx", "nova"])
def test_generate_runs_pipeline_once_in_qthread_and_saves_result(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    voice: str,
) -> None:
    result = LocalMediaResult(
        audio_path=tmp_path / "ready-audio" / "Einkaufen im Supermarkt.mp3",
        video_path=tmp_path / "ready-videos" / "Einkaufen im Supermarkt.mp4",
        alignment_score=100.0,
        whisper_strategy="word",
        video_sha256="video-sha256",
    )
    pipeline = _FakePipeline(result)
    window, pipeline_builder = _filled_window(
        pipeline=pipeline,
        monkeypatch=monkeypatch,
        topic="Einkaufen im Supermarkt",
    )
    window.text_input.setPlainText(
        "  Wir kaufen heute frisches Brot.\r\n\r\n"
        "Danach gehen wir nach Hause.\n   Morgen kommen wir wieder.  "
    )
    window.voice_input.setCurrentIndex(window.voice_input.findData(voice))
    assert window.voice_input.currentText().startswith(f"{voice} — ")

    window.generate_button.click()
    thread = window._generation_thread

    assert isinstance(thread, QThread)
    assert pipeline.started.wait(timeout=2.0)
    assert window.status_label.text() == "Generating..."
    assert not window._content_widget.isEnabled()
    assert not window._loading_overlay.isHidden()
    assert window._loading_overlay._spinner._timer.isActive()
    assert not window.generate_button.isEnabled()
    pipeline_builder.assert_called_once_with(
        title="Annas Brief",
        language="de",
        voice=voice,
        instructions="Speak clearly",
        playback_rate=0.95,
        karaoke_enabled=True,
    )
    assert pipeline.calls == [
        {
            "text": (
                "Wir kaufen heute frisches Brot. Danach gehen wir nach Hause. "
                "Morgen kommen wir wieder."
            ),
            "topic": "Einkaufen im Supermarkt",
            "background": Path("C:/images/supermarket.png"),
            "output_name": "Einkaufen im Supermarkt",
            "ready_audio_dir": admin_module.DEFAULT_READY_AUDIO_DIR,
            "ready_video_dir": admin_module.DEFAULT_READY_VIDEO_DIR,
            "tmp_dir": admin_module.DEFAULT_TMP_DIR,
        }
    ]

    window._prepare_generation()
    assert len(pipeline.calls) == 1
    pipeline_builder.assert_called_once()

    _release_and_wait_for_thread(
        qt_app=qt_app,
        pipeline=pipeline,
        thread=thread,
    )
    assert window.status_label.text() == "Ready"
    assert window._content_widget.isEnabled()
    assert window._loading_overlay.isHidden()
    assert not window._loading_overlay._spinner._timer.isActive()
    assert window.generate_button.isEnabled()
    assert window.last_media_result is result
    assert window._generation_thread is None
    assert window._generation_worker is None
    window.close()


def test_generate_failure_restores_gui_and_stops_qthread(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = "output_name contains unsupported file-name characters"
    pipeline = _FakePipeline(ValueError(message))
    window, _ = _filled_window(
        pipeline=pipeline,
        monkeypatch=monkeypatch,
        topic="Einkaufen: Supermarkt",
    )

    window.generate_button.click()
    thread = window._generation_thread

    assert isinstance(thread, QThread)
    assert pipeline.started.wait(timeout=2.0)
    assert not window._content_widget.isEnabled()
    assert not window._loading_overlay.isHidden()
    assert pipeline.calls[0]["output_name"] == "Einkaufen: Supermarkt"
    _release_and_wait_for_thread(
        qt_app=qt_app,
        pipeline=pipeline,
        thread=thread,
    )
    assert window.status_label.text() == f"Error: {message}"
    assert window._content_widget.isEnabled()
    assert window._loading_overlay.isHidden()
    assert not window._loading_overlay._spinner._timer.isActive()
    assert window.generate_button.isEnabled()
    assert window.last_media_result is None
    assert window._generation_thread is None
    assert window._generation_worker is None
    window.close()


def test_long_generation_error_does_not_expand_window_width(
    qt_app: QApplication,
) -> None:
    del qt_app
    window = admin_module.AdminWindow()
    try:
        initial_minimum_width = window.minimumSizeHint().width()
        message = (
            "Alignment remained below 97.00% after the primary and one alternate "
            "Whisper strategy. TTS was generated once and was not retried. "
            "Review the approved text/audio and rerun manually. Details: "
            "expected='Трав’яний' heard='Крав ямий'"
        )

        window._generation_failed(message)
        window.ensurePolished()
        window.adjustSize()

        assert window.status_label.wordWrap()
        assert window.status_label.sizePolicy().horizontalPolicy() == (
            admin_module.QSizePolicy.Policy.Ignored
        )
        assert window.minimumSizeHint().width() <= initial_minimum_width
    finally:
        window.close()


@pytest.mark.parametrize("rate", (0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20))
@pytest.mark.parametrize("language", ("uk", "pl", "nl"))
def test_language_and_custom_instructions_reach_production_adapters(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    language: str,
    rate: float,
) -> None:
    del qt_app
    adapter_targets = {
        "OpenAIAudioGenerator": "anna_backend.tts.client",
        "OpenAIWhisperWordTranscriber": "anna_backend.whisper.client",
        "FFmpegLearningAudioPacer": "anna_backend.audio.learning",
        "FFmpegReadyAudioEncoder": "anna_backend.audio.learning",
        "AssSubtitleRenderer": "anna_backend.subtitles.ass",
        "FFmpegVideoRenderer": "anna_backend.video.ffmpeg",
    }
    factories: dict[str, Mock] = {}
    adapters: dict[str, object] = {}
    for name, module_name in adapter_targets.items():
        adapter = object()
        factory = Mock(return_value=adapter)
        monkeypatch.setattr(import_module(module_name), name, factory)
        factories[name] = factory
        adapters[name] = adapter

    pipeline = Mock()
    pipeline_factory = Mock(return_value=pipeline)
    monkeypatch.setattr(
        import_module("anna_backend.local_pipeline"),
        "LocalPipeline",
        pipeline_factory,
    )
    monkeypatch.setattr(admin_module, "_api_key", lambda: "test-api-key")

    window = admin_module.AdminWindow()
    assert [
        window.voice_input.itemData(index)
        for index in range(window.voice_input.count())
    ] == list(admin_module.OPENAI_TTS_VOICES)
    assert window.voice_input.currentData() == "marin"
    for index in range(window.voice_input.count()):
        label = window.voice_input.itemText(index)
        assert any(timbre in label for timbre in ("жіночий", "чоловічий", "нейтральний"))
    window.voice_input.setCurrentIndex(window.voice_input.findData("coral"))
    window.language_input.setCurrentIndex(window.language_input.findData(language))

    built_pipeline = window._build_pipeline(
        title="Abends Brief",
        language=str(window.language_input.currentData()),
        voice=window.voice_input.currentData(),
        instructions="Custom speaking instructions",
        playback_rate=rate,
    )

    tts_call = factories["OpenAIAudioGenerator"].call_args
    assert tts_call.kwargs["api_key"] == "test-api-key"
    profile = tts_call.kwargs["profile"]
    assert isinstance(profile, SpeechProfile)
    assert profile.voice == "coral"
    assert profile.instructions == "Custom speaking instructions"
    factories["OpenAIWhisperWordTranscriber"].assert_called_once_with(
        api_key="test-api-key",
        language=language,
    )
    factories["FFmpegLearningAudioPacer"].assert_called_once_with(pace=LearningPace(playback_rate=rate))
    factories["FFmpegReadyAudioEncoder"].assert_called_once_with()
    factories["AssSubtitleRenderer"].assert_called_once_with(karaoke_enabled=True)
    factories["FFmpegVideoRenderer"].assert_called_once_with(
        font_dir=admin_module.DEFAULT_FONT_DIR,
        intro_title="Abends Brief",
    )
    pipeline_factory.assert_called_once_with(
        text_to_speech=adapters["OpenAIAudioGenerator"],
        transcriber=adapters["OpenAIWhisperWordTranscriber"],
        audio_pacer=adapters["FFmpegLearningAudioPacer"],
        ready_audio_encoder=adapters["FFmpegReadyAudioEncoder"],
        subtitle_renderer=adapters["AssSubtitleRenderer"],
        video_renderer=adapters["FFmpegVideoRenderer"],
    )
    assert built_pipeline is pipeline
    pipeline.run.assert_not_called()
    window.close()


def test_ukrainian_default_instructions_are_accepted_for_generation(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    result = _media_result(tmp_path, "ukrainian")
    pipeline = _FakePipeline(result)
    window, pipeline_builder = _filled_window(
        pipeline=pipeline,
        monkeypatch=monkeypatch,
        topic="Українська тема",
    )
    window.text_input.setPlainText("Сьогодні гарний день.")
    window.language_input.setCurrentIndex(window.language_input.findData("uk"))

    window.generate_button.click()
    thread = window._generation_thread

    assert isinstance(thread, QThread)
    assert pipeline.started.wait(timeout=2.0)
    pipeline_builder.assert_called_once_with(
        title="Annas Brief",
        language="uk",
        voice="marin",
        instructions=admin_module.LANGUAGE_INSTRUCTIONS["uk"],
        playback_rate=0.95,
        karaoke_enabled=True,
    )
    _release_and_wait_for_thread(
        qt_app=qt_app,
        pipeline=pipeline,
        thread=thread,
    )
    assert window.status_label.text() == "Ready"
    window.close()


@pytest.mark.parametrize("rate", (0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20))
def test_selected_speed_reaches_generation(qt_app, monkeypatch, rate):
    window = admin_module.AdminWindow()
    assert [window.speed_input.itemData(i) for i in range(window.speed_input.count())] == [
        0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20
    ]
    assert window.speed_input.currentData() == 0.95
    window.topic_input.setText("Test")
    window.text_input.setPlainText("Approved text.")
    window.background_input.setText("background.png")
    window.speed_input.setCurrentIndex(window.speed_input.findData(rate))
    builder = Mock(side_effect=ValueError("stop before paid calls"))
    monkeypatch.setattr(window, "_build_pipeline", builder)
    window.generate_button.click()
    assert builder.call_args.kwargs["playback_rate"] == rate
    window.close()


def _add_test_background(window, monkeypatch, path, color="magenta"):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(40, 20, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    assert image.save(str(path))
    monkeypatch.setattr(
        admin_module.QFileDialog, "getOpenFileName",
        Mock(return_value=(str(path), "Images (*.png)")),
    )
    window.choose_image_button.click()
    return Path(window.background_input.text())


def test_saved_background_survives_reopening_and_reaches_pipeline(qt_app, monkeypatch, tmp_path):
    window = admin_module.AdminWindow()
    original = tmp_path / "original.png"
    saved = _add_test_background(window, monkeypatch, original)
    original.unlink()
    window.close()

    pipeline = _FakePipeline(_media_result(tmp_path, "saved-background"))
    reopened, _ = _filled_window(pipeline=pipeline, monkeypatch=monkeypatch, topic="Saved image")
    index = reopened.saved_backgrounds.findData(str(saved))
    assert index > 0
    reopened.saved_backgrounds.setCurrentIndex(index)
    assert reopened.background_input.text() == str(saved)
    assert not reopened.background_preview.pixmap().isNull()
    reopened.generate_button.click()
    thread = reopened._generation_thread
    assert thread is not None
    _release_and_wait_for_thread(qt_app=qt_app, pipeline=pipeline, thread=thread)
    assert len(pipeline.calls) == 1
    assert pipeline.calls[0]["background"] == saved
    reopened.close()


def test_saved_backgrounds_with_same_name_remain_independently_selectable(
    qt_app, monkeypatch, tmp_path,
):
    window = admin_module.AdminWindow()
    first = _add_test_background(window, monkeypatch, tmp_path / "one" / "image.png", "red")
    original_bytes = first.read_bytes()
    second = _add_test_background(window, monkeypatch, tmp_path / "two" / "image.png", "blue")
    assert first != second
    assert first.read_bytes() == original_bytes
    assert first.read_bytes() != second.read_bytes()
    assert window.saved_backgrounds.count() == 3
    window.saved_backgrounds.setCurrentIndex(window.saved_backgrounds.findData(str(first)))
    assert window.background_input.text() == str(first)
    assert window.background_preview.pixmap().toImage().pixelColor(0, 0) == QColor("red")
    window.close()


def test_invalid_background_does_not_change_selection(qt_app, monkeypatch, tmp_path):
    window = admin_module.AdminWindow()
    invalid = tmp_path / "invalid.png"
    invalid.write_text("not an image", encoding="utf-8")
    monkeypatch.setattr(
        admin_module.QFileDialog, "getOpenFileName",
        Mock(return_value=(str(invalid), "Images (*.png)")),
    )
    window.choose_image_button.click()
    assert window.background_input.text() == str(admin_module.DEFAULT_BACKGROUND_IMAGE)
    assert window.saved_backgrounds.count() == 1
    assert "Cannot add image" in window.status_label.text()
    assert not admin_module.SAVED_BACKGROUNDS_DIR.exists()
    window.close()


def test_karaoke_defaults_on_and_selected_state_reaches_generation(qt_app, monkeypatch):
    from PySide6.QtCore import QEvent
    from PySide6.QtTest import QTest

    window = admin_module.AdminWindow()
    try:
        assert window.karaoke_input.isChecked()
        window.topic_input.setText("Karaoke check")
        window.text_input.setPlainText("Hallo Welt.")
        builder = Mock(side_effect=ValueError("stop before paid calls"))
        monkeypatch.setattr(window, "_build_pipeline", builder)
        window.show()
        qt_app.processEvents()
        for enabled in (False, True):
            QTest.mouseClick(window.karaoke_input, Qt.MouseButton.LeftButton)
            assert window.karaoke_input.isChecked() is enabled
            QTest.mouseClick(window.generate_button, Qt.MouseButton.LeftButton)
            assert builder.call_args.kwargs["karaoke_enabled"] is enabled
    finally:
        window.close()
        window.deleteLater()
        qt_app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
