from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from anna_backend.audio.learning import (
    FFmpegLearningAudioPacer,
    FFmpegReadyAudioEncoder,
)
from anna_backend.execution_log import execution_session, operation
from anna_backend.local_pipeline import LocalPipeline
from anna_backend.media_types import LocalMediaResult
from anna_backend.subtitles.ass import AssSubtitleRenderer
from anna_backend.tts.client import OpenAIAudioGenerator
from anna_backend.tts.profile import ANNA_MARIN_PROFILE, SpeechProfile
from anna_backend.video.ffmpeg import FFmpegVideoRenderer
from anna_backend.whisper.client import OpenAIWhisperWordTranscriber

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FONT_DIR = _BACKEND_ROOT / "resources" / "fonts"
DEFAULT_READY_AUDIO_DIR = _BACKEND_ROOT / "ready-audio"
DEFAULT_READY_VIDEO_DIR = _BACKEND_ROOT / "ready-videos"
DEFAULT_TMP_DIR = _BACKEND_ROOT / "tmp"


class _CliSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    openai_api_key: str | None = None


class _CliError(RuntimeError):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate one local learning-audio file and karaoke video."
    )
    text_source = parser.add_mutually_exclusive_group(required=True)
    text_source.add_argument(
        "--text",
        help="Approved text to render.",
    )
    text_source.add_argument(
        "--text-file",
        type=Path,
        help="UTF-8 file containing the approved text to render.",
    )
    parser.add_argument(
        "--topic",
        required=True,
        help="Topic shown below Anna's Brief in the video intro.",
    )
    parser.add_argument(
        "--background",
        "--image",
        required=True,
        type=Path,
        help="Local background image.",
    )
    parser.add_argument(
        "--output-name",
        "--name",
        required=True,
        help="Base name for the ready audio and video files.",
    )
    parser.add_argument(
        "--language",
        choices=("de", "en", "uk", "pl", "nl"),
        default="de",
        help="ISO language code used for Whisper transcription (default: de).",
    )
    parser.add_argument(
        "--instructions",
        help="TTS speaking instructions; required for non-German languages.",
    )
    return parser


def _read_text(arguments: argparse.Namespace) -> str:
    if arguments.text is not None:
        text = arguments.text
    else:
        text_file: Path = arguments.text_file
        try:
            text = text_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise _CliError(f"Could not read UTF-8 text file '{text_file}': {error}") from error

        if text.endswith("\r\n"):
            text = text[:-2]
        elif text.endswith(("\r", "\n")):
            text = text[:-1]

    if not text.strip():
        raise _CliError("Text must not be empty.")
    return text


def _api_key() -> str:
    configured_key = _CliSettings().openai_api_key
    if configured_key is None:
        raise _CliError(
            "OPENAI_API_KEY is required in the process environment or backend/.env."
        )

    api_key = configured_key.strip()
    if not api_key:
        raise _CliError(
            "OPENAI_API_KEY is required in the process environment or backend/.env."
        )
    return api_key


def _result_payload(result: LocalMediaResult) -> dict[str, str | float]:
    return {
        "audio_path": str(result.audio_path),
        "video_path": str(result.video_path),
        "alignment_score": result.alignment_score,
        "whisper_strategy": result.whisper_strategy,
        "video_sha256": result.video_sha256,
    }


def _result_json(
    result: LocalMediaResult,
    *,
    output_encoding: str | None = None,
) -> str:
    payload = json.dumps(_result_payload(result), ensure_ascii=False)
    encoding = output_encoding or getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        payload.encode(encoding)
    except UnicodeEncodeError:
        return json.dumps(_result_payload(result), ensure_ascii=True)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    with execution_session(DEFAULT_TMP_DIR.expanduser().resolve().parent / "logs"):
        return _main(argv)


def _main(argv: Sequence[str] | None = None) -> int:
    with operation("parse_arguments"):
        arguments = _parser().parse_args(argv)
    try:
        with operation("CLI preparation and generation"):
            text = _read_text(arguments)
            if arguments.language != "de" and arguments.instructions is None:
                raise _CliError(
                    "--instructions is required when --language is not de."
                )
            if arguments.instructions is None:
                speech_profile = ANNA_MARIN_PROFILE
            else:
                speech_profile = SpeechProfile(
                    model=ANNA_MARIN_PROFILE.model,
                    voice=ANNA_MARIN_PROFILE.voice,
                    instructions=arguments.instructions,
                    response_format=ANNA_MARIN_PROFILE.response_format,
                    speed=ANNA_MARIN_PROFILE.speed,
                )
            api_key = _api_key()
            pipeline = LocalPipeline(
                text_to_speech=OpenAIAudioGenerator(
                    api_key=api_key,
                    profile=speech_profile,
                ),
                transcriber=OpenAIWhisperWordTranscriber(
                    api_key=api_key,
                    language=arguments.language,
                ),
                audio_pacer=FFmpegLearningAudioPacer(),
                ready_audio_encoder=FFmpegReadyAudioEncoder(),
                subtitle_renderer=AssSubtitleRenderer(),
                video_renderer=FFmpegVideoRenderer(font_dir=DEFAULT_FONT_DIR),
            )
            result = asyncio.run(
                pipeline.run(
                    text=text,
                    topic=arguments.topic,
                    background=arguments.background,
                    output_name=arguments.output_name,
                    ready_audio_dir=DEFAULT_READY_AUDIO_DIR,
                    ready_video_dir=DEFAULT_READY_VIDEO_DIR,
                    tmp_dir=DEFAULT_TMP_DIR,
                )
            )
    except (RuntimeError, ValueError, FileNotFoundError, OSError) as error:
        print(f"make_video: error: {error}", file=sys.stderr)
        return 2

    print(_result_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
