from __future__ import annotations

import asyncio
import shutil
import sys
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic_settings import BaseSettings, SettingsConfigDict
from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from anna_backend.media_types import LocalMediaResult
from anna_backend.tts.profile import ANNA_MARIN_PROFILE, SPEAKING_ROLES, SpeechProfile

if TYPE_CHECKING:
    from anna_backend.local_pipeline import LocalPipeline


_BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FONT_DIR = _BACKEND_ROOT / "resources" / "fonts"
DEFAULT_READY_AUDIO_DIR = _BACKEND_ROOT / "ready-audio"
DEFAULT_READY_VIDEO_DIR = _BACKEND_ROOT / "ready-videos"
DEFAULT_TMP_DIR = _BACKEND_ROOT / "tmp"
DEFAULT_BACKGROUND_IMAGE = _BACKEND_ROOT / "resources" / "images" / "Anna.png"
SAVED_BACKGROUNDS_DIR = _BACKEND_ROOT / "saved-backgrounds"
LANGUAGE_OPTIONS = (
    ("German", "de"),
    ("English", "en"),
    ("Ukrainian", "uk"),
    ("Polish", "pl"),
    ("Dutch", "nl"),
)
LANGUAGE_INSTRUCTIONS = {
    "de": ANNA_MARIN_PROFILE.instructions,
    "uk": (
        "Чоловічий голос 35–45 років. Низький, впевнений, серйозний і "
        "авторитетний. Манера — сувора, але спокійна, без агресії та крику. "
        "Говорить так, ніби повідомляє важливу річ, яку слухач повинен "
        "запам’ятати.\n"
        "\n"
        "Темп середній, на ключових думках трохи сповільнюється. Важливі слова "
        "природно підкреслює голосом і короткими паузами. Інтонація жива: "
        "попередження — твердіше, небезпека — серйозніше, пояснення — спокійно й "
        "переконливо, практична порада — чітко та впевнено.\n"
        "\n"
        "Не читати монотонно й не звучати як диктор новин. Має виникати відчуття "
        "особистого звернення прямо до глядача: «послухай уважно — це справді "
        "важливо». Кожна головна думка повинна мати голосовий акцент і легко "
        "запам’ятовуватися."
    ),
}
# Approximate listening guides, not provider-defined gender or style guarantees.
OPENAI_TTS_VOICES = {
    "alloy": "нейтральний, рівний, спокійний",
    "ash": "чоловічий, стриманий, м’який",
    "ballad": "чоловічий, теплий, мелодійний",
    "coral": "жіночий, яскравий, дружній",
    "echo": "чоловічий, чіткий, рівний",
    "fable": "чоловічий, виразний, оповідний",
    "onyx": "чоловічий, низький, насичений",
    "nova": "жіночий, жвавий, виразний",
    "sage": "жіночий, м’який, спокійний",
    "shimmer": "жіночий, легкий, дзвінкий",
    "verse": "чоловічий, розмовний, гнучкий",
    "marin": "жіночий, теплий, природний",
    "cedar": "чоловічий, теплий, глибокий",
}


class _AdminSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    openai_api_key: str | None = None


def _api_key() -> str:
    configured_key = _AdminSettings().openai_api_key
    if configured_key is None or not configured_key.strip():
        raise RuntimeError("OPENAI_API_KEY is required in the process environment or backend/.env.")
    return configured_key.strip()


class _GenerationWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        *,
        pipeline: LocalPipeline,
        text: str,
        topic: str,
        background: Path,
        output_name: str,
        ready_audio_dir: Path,
        ready_video_dir: Path,
        tmp_dir: Path,
    ) -> None:
        super().__init__()
        self._pipeline = pipeline
        self._text = text
        self._topic = topic
        self._background = background
        self._output_name = output_name
        self._ready_audio_dir = ready_audio_dir
        self._ready_video_dir = ready_video_dir
        self._tmp_dir = tmp_dir

    @Slot()
    def run(self) -> None:
        try:
            result = asyncio.run(
                self._pipeline.run(
                    text=self._text,
                    topic=self._topic,
                    background=self._background,
                    output_name=self._output_name,
                    ready_audio_dir=self._ready_audio_dir,
                    ready_video_dir=self._ready_video_dir,
                    tmp_dir=self._tmp_dir,
                )
            )
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(result)
        finally:
            self.finished.emit()


def _normalize_manual_text(value: str) -> str:
    return " ".join(value.split())


class _Spinner(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(48, 48)
        self._step = 0
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._advance)

    def start(self) -> None:
        self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()

    def _advance(self) -> None:
        self._step = (self._step + 1) % 12
        self.update()

    def paintEvent(self, event: object) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.translate(self.width() / 2, self.height() / 2)

        for index in range(12):
            phase = (index - self._step) % 12
            color = QColor(255, 255, 255, 255 - (phase * 18))
            painter.setBrush(color)
            painter.save()
            painter.rotate(index * 30)
            painter.drawRoundedRect(-3, -20, 6, 11, 3, 3)
            painter.restore()


class _LoadingOverlay(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("loadingOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(
            "#loadingOverlay { background-color: rgba(0, 0, 0, 150); }"
            "#loadingOverlay QLabel { background: transparent; color: white; }"
        )

        self._spinner = _Spinner()
        message = QLabel("Generating video...")
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.addStretch()
        layout.addWidget(self._spinner, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(message, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()
        self.hide()

    def start(self) -> None:
        self.show()
        self.setFocus()
        self._spinner.start()

    def stop(self) -> None:
        self._spinner.stop()
        self.hide()


class AdminWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Anna Video Admin")
        self.resize(1100, 760)
        self.last_media_result: LocalMediaResult | None = None
        self._generation_thread: QThread | None = None
        self._generation_worker: _GenerationWorker | None = None

        self.title_input = QLineEdit("Annas Brief")
        self.topic_input = QLineEdit()
        self.text_input = QTextEdit()
        self.text_input.setFixedHeight(120)
        self.language_input = QComboBox()
        for label, code in LANGUAGE_OPTIONS:
            self.language_input.addItem(label, code)
        self.role_input = QComboBox()
        self.role_input.addItem("Поточний профіль (Анна / за мовою)", "default")
        for role, (label, _) in SPEAKING_ROLES.items():
            self.role_input.addItem(label, role)
        self.role_input.setToolTip(
            "Нові ролі працюють з будь-якою обраною мовою та голосом. "
            "Зміна ролі або мови замінює текст Instructions відповідним шаблоном."
        )
        self.speed_input = QComboBox()
        for rate in (0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20):
            self.speed_input.addItem(f"{rate:.2f}".replace(".", ","), rate)
        self.speed_input.setCurrentIndex(self.speed_input.findData(0.95))
        self.speed_input.setToolTip(
            "Speech speed in the saved audio and video: below 1.00 is slower; "
            "above 1.00 is faster. Sentence pauses stay unchanged."
        )
        self.karaoke_input = QCheckBox("Караоке")
        self.karaoke_input.setChecked(True)
        self.karaoke_input.setToolTip("Зніміть прапорець для відео без тексту караоке.")
        speed_controls = QWidget()
        speed_layout = QHBoxLayout(speed_controls)
        speed_layout.setContentsMargins(0, 0, 0, 0)
        speed_layout.addWidget(self.speed_input, 1)
        speed_layout.addWidget(self.karaoke_input)
        self.voice_input = QComboBox()
        for voice, description in OPENAI_TTS_VOICES.items():
            self.voice_input.addItem(f"{voice} — {description}", voice)
        self.voice_input.setCurrentIndex(self.voice_input.findData("marin"))
        self.voice_input.setToolTip(
            "Орієнтовний опис тембру. Звучання залежить від мови та інструкцій озвучення."
        )
        self.instructions_input = QTextEdit()
        self.language_input.currentIndexChanged.connect(self._apply_default_instructions)
        self.role_input.currentIndexChanged.connect(self._apply_default_instructions)
        self._apply_default_instructions()
        self.instructions_input.setFixedHeight(120)
        self.background_input = QLineEdit(str(DEFAULT_BACKGROUND_IMAGE))
        self.background_input.setReadOnly(True)

        self.background_preview = QLabel()
        self.background_preview.setFixedSize(320, 180)
        self.background_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.background_preview.setFrameShape(QFrame.Shape.Box)
        self._update_background_preview(DEFAULT_BACKGROUND_IMAGE)
        self.saved_backgrounds = QComboBox()
        self.saved_backgrounds.addItem("Anna (default)", str(DEFAULT_BACKGROUND_IMAGE))
        if SAVED_BACKGROUNDS_DIR.is_dir():
            for image in sorted(SAVED_BACKGROUNDS_DIR.iterdir()):
                if image.is_file() and image.suffix.lower() in {
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".webp",
                    ".bmp",
                }:
                    self.saved_backgrounds.addItem(image.name, str(image))
        self.saved_backgrounds.currentIndexChanged.connect(self._select_saved_background)
        self.choose_image_button = QPushButton("Add image…")
        self.choose_image_button.clicked.connect(self._choose_image)
        background_section = QWidget()
        background_layout = QVBoxLayout(background_section)
        background_layout.setContentsMargins(0, 0, 0, 0)
        background_layout.addWidget(self.background_preview)
        background_layout.addWidget(self.saved_backgrounds)
        background_layout.addWidget(self.choose_image_button)
        background_layout.addWidget(self.background_input)

        form_layout = QFormLayout()
        form_layout.addRow("Title", self.title_input)
        form_layout.addRow("Topic", self.topic_input)
        form_layout.addRow("Text", self.text_input)
        form_layout.addRow("Language", self.language_input)
        form_layout.addRow("Role", self.role_input)
        form_layout.addRow("Voice", self.voice_input)
        form_layout.addRow("Speed", speed_controls)
        form_layout.addRow("Instructions", self.instructions_input)
        form_layout.addRow("Background image", background_section)

        self.generate_button = QPushButton("Generate video")
        self.generate_button.setMinimumHeight(40)
        self.generate_button.clicked.connect(self._prepare_generation)
        self.status_label = QLabel("Waiting for input")
        self.status_label.setWordWrap(True)
        status_policy = self.status_label.sizePolicy()
        status_policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        self.status_label.setSizePolicy(status_policy)

        self.audio_output = QAudioOutput(self)
        self.audio_player = QMediaPlayer(self)
        self.audio_player.setAudioOutput(self.audio_output)
        self.play_audio_button = QPushButton("Play audio")
        self.play_audio_button.setEnabled(False)
        self.play_audio_button.clicked.connect(self.audio_player.play)
        self.stop_audio_button = QPushButton("Stop audio")
        self.stop_audio_button.setEnabled(False)
        self.stop_audio_button.clicked.connect(self.audio_player.stop)
        audio_controls = QWidget()
        audio_controls_layout = QHBoxLayout(audio_controls)
        audio_controls_layout.setContentsMargins(0, 0, 0, 0)
        audio_controls_layout.addWidget(self.play_audio_button)
        audio_controls_layout.addWidget(self.stop_audio_button)

        self.video_output = QAudioOutput(self)
        self.video_player = QMediaPlayer(self)
        self.video_player.setAudioOutput(self.video_output)
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumSize(360, 640)
        self.video_player.setVideoOutput(self.video_widget)
        self.play_video_button = QPushButton("Play video")
        self.play_video_button.setEnabled(False)
        self.play_video_button.clicked.connect(self.video_player.play)
        self.stop_video_button = QPushButton("Stop video")
        self.stop_video_button.setEnabled(False)
        self.stop_video_button.clicked.connect(self.video_player.stop)
        video_controls = QWidget()
        video_controls_layout = QHBoxLayout(video_controls)
        video_controls_layout.setContentsMargins(0, 0, 0, 0)
        video_controls_layout.addWidget(self.play_video_button)
        video_controls_layout.addWidget(self.stop_video_button)

        left_layout = QVBoxLayout()
        left_layout.addLayout(form_layout)
        left_layout.addWidget(self.generate_button)
        left_layout.addWidget(self.status_label)
        left_layout.addWidget(QLabel("Audio"))
        left_layout.addWidget(audio_controls)

        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("Video"))
        right_layout.addWidget(self.video_widget)
        right_layout.addWidget(video_controls)

        layout = QHBoxLayout()
        layout.addLayout(left_layout, 1)
        layout.addLayout(right_layout)

        self._content_widget = QWidget()
        self._content_widget.setLayout(layout)
        self._loading_overlay = _LoadingOverlay()

        central_widget = QWidget()
        self._stack_layout = QStackedLayout(central_widget)
        self._stack_layout.setContentsMargins(0, 0, 0, 0)
        self._stack_layout.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self._stack_layout.addWidget(self._content_widget)
        self._stack_layout.addWidget(self._loading_overlay)
        self._stack_layout.setCurrentWidget(self._content_widget)
        self.setCentralWidget(central_widget)

    def _choose_image(self) -> None:
        image_path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose background image",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All files (*)",
        )
        if not image_path:
            return
        source = Path(image_path).resolve()
        if source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} or (
            QPixmap(str(source)).isNull()
        ):
            self.status_label.setText("Cannot add image: choose a valid PNG, JPG, WEBP or BMP.")
            return
        try:
            SAVED_BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)
            if source.parent == SAVED_BACKGROUNDS_DIR.resolve():
                destination = source
            else:
                destination = SAVED_BACKGROUNDS_DIR / source.name
                number = 2
                while destination.exists():
                    destination = SAVED_BACKGROUNDS_DIR / (
                        f"{source.stem} ({number}){source.suffix}"
                    )
                    number += 1
                try:
                    shutil.copy2(source, destination)
                except OSError:
                    with suppress(OSError):
                        destination.unlink(missing_ok=True)
                    raise
        except OSError as error:
            self.status_label.setText(f"Cannot save background image: {error}")
            return
        index = self.saved_backgrounds.findData(str(destination))
        if index < 0:
            self.saved_backgrounds.addItem(destination.name, str(destination))
            index = self.saved_backgrounds.count() - 1
        self.saved_backgrounds.setCurrentIndex(index)
        self.status_label.setText(f"Background saved: {destination.name}")

    def _select_saved_background(self, index: int) -> None:
        image_path = self.saved_backgrounds.itemData(index)
        if image_path:
            self.background_input.setText(image_path)
            self._update_background_preview(image_path)

    def _update_background_preview(self, image_path: str | Path) -> None:
        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            self.background_preview.clear()
            return
        self.background_preview.setPixmap(
            pixmap.scaled(
                self.background_preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _apply_default_instructions(self) -> None:
        language = str(self.language_input.currentData())
        instructions = LANGUAGE_INSTRUCTIONS.get(
            language,
            ANNA_MARIN_PROFILE.instructions,
        )
        role = str(self.role_input.currentData())
        if role in SPEAKING_ROLES:
            instructions = (
                f"Мова озвучення: {self.language_input.currentText()} ({language}). "
                "Використовуй природну вимову обраної мови. Читай наданий текст дослівно: "
                "не перекладай, не перефразовуй, не додавай і не пропускай слова. "
                "Ці інструкції визначають лише манеру озвучення; не вимовляй їх уголос.\n\n"
                + SPEAKING_ROLES[role][1]
            )
        self.instructions_input.setPlainText(instructions)

    def _build_pipeline(
        self,
        *,
        title: str,
        language: str,
        voice: str,
        instructions: str,
        playback_rate: float = 0.95,
        karaoke_enabled: bool = True,
    ) -> LocalPipeline:
        from anna_backend.audio.learning import (
            FFmpegLearningAudioPacer,
            FFmpegReadyAudioEncoder,
            LearningPace,
        )
        from anna_backend.local_pipeline import LocalPipeline
        from anna_backend.subtitles.ass import AssSubtitleRenderer
        from anna_backend.tts.client import OpenAIAudioGenerator
        from anna_backend.video.ffmpeg import FFmpegVideoRenderer
        from anna_backend.whisper.client import OpenAIWhisperWordTranscriber

        api_key = _api_key()
        speech_profile = SpeechProfile(
            model=ANNA_MARIN_PROFILE.model,
            voice=voice,
            instructions=instructions,
            response_format=ANNA_MARIN_PROFILE.response_format,
            speed=ANNA_MARIN_PROFILE.speed,
        )
        return LocalPipeline(
            text_to_speech=OpenAIAudioGenerator(
                api_key=api_key,
                profile=speech_profile,
            ),
            transcriber=OpenAIWhisperWordTranscriber(
                api_key=api_key,
                language=language,
            ),
            audio_pacer=FFmpegLearningAudioPacer(pace=LearningPace(playback_rate=playback_rate)),
            ready_audio_encoder=FFmpegReadyAudioEncoder(),
            subtitle_renderer=AssSubtitleRenderer(karaoke_enabled=karaoke_enabled),
            video_renderer=FFmpegVideoRenderer(
                font_dir=DEFAULT_FONT_DIR,
                intro_title=title,
            ),
        )

    def _prepare_generation(self) -> None:
        if self._generation_thread is not None:
            return

        values = {
            "title": self.title_input.text().strip(),
            "topic": self.topic_input.text().strip(),
            "text": _normalize_manual_text(self.text_input.toPlainText()),
            "language": self.language_input.currentData(),
            "voice": self.voice_input.currentData(),
            "instructions": self.instructions_input.toPlainText().strip(),
            "background_image": self.background_input.text().strip(),
        }
        required_fields = (
            ("topic", "Topic is required"),
            ("text", "Text is required"),
            ("background_image", "Background image is required"),
        )
        for field, message in required_fields:
            if not values[field]:
                self.status_label.setText(message)
                return
        try:
            pipeline = self._build_pipeline(
                title=values["title"],
                language=str(values["language"]),
                voice=values["voice"],
                instructions=values["instructions"],
                playback_rate=float(self.speed_input.currentData()),
                karaoke_enabled=self.karaoke_input.isChecked(),
            )
        except (RuntimeError, ValueError) as error:
            self.status_label.setText(str(error))
            return

        thread = QThread(self)
        worker = _GenerationWorker(
            pipeline=pipeline,
            text=values["text"],
            topic=values["topic"],
            background=Path(values["background_image"]),
            output_name=values["topic"],
            ready_audio_dir=DEFAULT_READY_AUDIO_DIR,
            ready_video_dir=DEFAULT_READY_VIDEO_DIR,
            tmp_dir=DEFAULT_TMP_DIR,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._generation_succeeded)
        worker.failed.connect(self._generation_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._generation_finished)
        thread.finished.connect(thread.deleteLater)

        self._generation_thread = thread
        self._generation_worker = worker
        self.status_label.setText("Generating...")
        self._content_widget.setEnabled(False)
        self._loading_overlay.start()
        self._stack_layout.setCurrentWidget(self._loading_overlay)
        thread.start()

    @Slot(object)
    def _generation_succeeded(self, result: LocalMediaResult) -> None:
        self.audio_player.stop()
        self.video_player.stop()
        self.audio_player.setSource(QUrl.fromLocalFile(str(result.audio_path)))
        self.video_player.setSource(QUrl.fromLocalFile(str(result.video_path)))
        self.play_audio_button.setEnabled(True)
        self.stop_audio_button.setEnabled(True)
        self.play_video_button.setEnabled(True)
        self.stop_video_button.setEnabled(True)
        self.last_media_result = result
        self.status_label.setText("Ready")

    @Slot(str)
    def _generation_failed(self, message: str) -> None:
        self.status_label.setText(f"Error: {message}")

    @Slot()
    def _generation_finished(self) -> None:
        self._loading_overlay.stop()
        self._stack_layout.setCurrentWidget(self._content_widget)
        self._content_widget.setEnabled(True)
        self._generation_worker = None
        self._generation_thread = None


def main() -> int:
    app = QApplication(sys.argv)
    window = AdminWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
