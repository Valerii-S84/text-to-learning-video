from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QEventLoop, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import anna_backend.admin_app as admin_module
import anna_backend.tts.client as tts_module
import anna_backend.whisper.client as whisper_module


def test_gui_creates_validated_mp4_with_karaoke_on_and_off(tmp_path, monkeypatch):
    fixture_dir = Path(__file__).parents[1] / "fixtures" / "render"
    fixture = json.loads((fixture_dir / "sample.json").read_text(encoding="utf-8"))
    approved_text = fixture["content"]["ttsText"]
    calls = {"tts": [], "whisper": []}

    class FrozenTTS:
        def __init__(self, **kwargs):
            pass

        async def synthesize(self, text, destination):
            calls["tts"].append(text)
            shutil.copyfile(fixture_dir / "sample.mp3", destination)
            return destination

    class FrozenWhisper:
        def __init__(self, **kwargs):
            pass

        async def transcribe(self, audio_path, *, strategy):
            calls["whisper"].append(strategy)
            return whisper_module.parse_word_timestamps(
                {"words": fixture["whisper"]["words"]}
            )

    monkeypatch.setattr(tts_module, "OpenAIAudioGenerator", FrozenTTS)
    monkeypatch.setattr(whisper_module, "OpenAIWhisperWordTranscriber", FrozenWhisper)
    monkeypatch.setattr(admin_module, "_api_key", lambda: "fixture-no-network")
    monkeypatch.setattr(admin_module, "SAVED_BACKGROUNDS_DIR", tmp_path / "backgrounds")
    monkeypatch.setattr(admin_module, "DEFAULT_READY_AUDIO_DIR", tmp_path / "ready-audio")
    monkeypatch.setattr(admin_module, "DEFAULT_READY_VIDEO_DIR", tmp_path / "ready-videos")
    monkeypatch.setattr(admin_module, "DEFAULT_TMP_DIR", tmp_path / "work")
    app = QApplication.instance() or QApplication([])
    report = []
    for enabled in (True, False):
        label = "on" if enabled else "off"
        window = admin_module.AdminWindow()
        try:
            window.topic_input.setText("Karaoke check " + label)
            window.text_input.setPlainText(approved_text)
            window.show()
            app.processEvents()
            if not enabled:
                QTest.mouseClick(window.karaoke_input, Qt.MouseButton.LeftButton)
            assert window.karaoke_input.isChecked() is enabled
            window.grab().save(str(tmp_path / ("ui-" + label + ".png")))
            print("GUI_RENDER_START " + label, flush=True)
            QTest.mouseClick(window.generate_button, Qt.MouseButton.LeftButton)
            thread = window._generation_thread
            assert thread is not None, window.status_label.text()
            loop = QEventLoop()
            thread.finished.connect(loop.quit)
            loop.exec()
            app.processEvents()
            assert window.status_label.text() == "Ready", window.status_label.text()
            assert window._generation_thread is None
            assert window.generate_button.isEnabled()
            result = window.last_media_result
            assert result is not None
            assert result.audio_path.stat().st_size > 0
            assert result.video_path.stat().st_size > 0
            assert result.alignment_score >= 97
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "stream=codec_type,codec_name,width,height,sample_rate:format=duration,size",
                 "-of", "json", str(result.video_path)],
                check=True, capture_output=True, text=True, timeout=30,
            )
            metadata = json.loads(probe.stdout)
            streams = metadata["streams"]
            assert [(s["codec_type"], s["codec_name"]) for s in streams] == [
                ("video", "h264"), ("audio", "aac"),
            ]
            assert (streams[0]["width"], streams[0]["height"]) == (1080, 1920)
            assert float(metadata["format"]["duration"]) > 0
            frame = tmp_path / ("frame-" + label + ".png")
            subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", "5", "-i", str(result.video_path),
                 "-frames:v", "1", str(frame)],
                check=True, capture_output=True, timeout=30,
            )
            report.append({"karaoke": enabled, "video": str(result.video_path),
                           "audio": str(result.audio_path), "frame": str(frame),
                           "ffprobe": metadata, "alignment_score": result.alignment_score})
            print("GUI_RENDER_PASS " + label, flush=True)
        finally:
            window.close()
            window.deleteLater()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert calls["tts"] == [approved_text, approved_text]
    assert calls["whisper"] == ["word", "word"]
    assert list((tmp_path / "work").iterdir()) == []
    (tmp_path / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
