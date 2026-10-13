from __future__ import annotations

import json
import re
import subprocess
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import pytest

import anna_backend.audio.learning as audio_module
import anna_backend.video.ffmpeg as video_module
from anna_backend.alignment import AlignmentError, align_spoken_text
from anna_backend.audio import (
    FFmpegReadyAudioEncoder,
    LearningPace,
    ReadyAudioEncodingError,
    build_learning_timeline,
)
from anna_backend.media_types import AlignmentResult, WordTiming
from anna_backend.subtitles import AssStyle, build_cues, render_ass
from anna_backend.whisper import (
    OpenAIWhisperWordTranscriber,
    WhisperResponseError,
    parse_word_timestamps,
)

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "render" / "sample.json"


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _sample_alignment() -> AlignmentResult:
    fixture = _fixture()
    content = fixture["content"]
    whisper = fixture["whisper"]
    assert isinstance(content, dict)
    assert isinstance(whisper, dict)
    words = parse_word_timestamps({"words": whisper["words"]})
    return align_spoken_text(str(content["ttsText"]), words, minimum_score=97.0)


def test_alignment_uses_frozen_text_with_whisper_timings() -> None:
    alignment = _sample_alignment()

    assert alignment.score == pytest.approx(98.6395)
    rendered_words = " ".join(item.word for item in alignment.words)
    assert "Der Kunde" in rendered_words
    assert "gab ich ihm" in rendered_words
    assert "was mir half." in rendered_words
    assert "gute Lektion" in rendered_words
    assert "Die Kunde" not in rendered_words
    assert "Nektion" not in rendered_words
    assert alignment.words[0].start_seconds == pytest.approx(0.19)
    assert alignment.words[-1].end_seconds == pytest.approx(36.84)


def test_alignment_fails_closed_below_threshold() -> None:
    words = (
        WordTiming(word="completely", start_seconds=0.0, end_seconds=0.5),
        WordTiming(word="different", start_seconds=0.5, end_seconds=1.0),
    )

    with pytest.raises(AlignmentError, match="below the required"):
        align_spoken_text("Hallo schöne Welt.", words, minimum_score=97.0)


def test_alignment_rejects_three_unmatched_terminal_words_above_threshold() -> None:
    matched_words = [
        f"wort{chr(97 + index // 26)}{chr(97 + index % 26)}" for index in range(105)
    ]
    target_words = [*matched_words, "bis", "bald", "Maria"]
    transcript = tuple(
        WordTiming(word=word, start_seconds=index * 0.1, end_seconds=(index + 1) * 0.1)
        for index, word in enumerate(matched_words)
    )

    assert 100.0 * len(matched_words) / len(target_words) == pytest.approx(97.2222)
    with pytest.raises(AlignmentError, match="does not confirm the end"):
        align_spoken_text(" ".join(target_words), transcript, minimum_score=97.0)


def test_alignment_still_interpolates_internal_omission_above_threshold() -> None:
    matched_words = [
        f"wort{chr(97 + index // 26)}{chr(97 + index % 26)}" for index in range(105)
    ]
    target_words = [
        *matched_words[:50],
        "internal",
        "omitted",
        "words",
        *matched_words[50:],
    ]
    transcript = tuple(
        WordTiming(
            word=word,
            start_seconds=(index + (3 if index >= 50 else 0)) * 0.1,
            end_seconds=(index + 1 + (3 if index >= 50 else 0)) * 0.1,
        )
        for index, word in enumerate(matched_words)
    )

    alignment = align_spoken_text(" ".join(target_words), transcript, minimum_score=97.0)

    assert alignment.score == pytest.approx(97.2222)
    assert [word.word for word in alignment.words[50:53]] == [
        "internal",
        "omitted",
        "words",
    ]
    assert alignment.words[-1].word == matched_words[-1]


def test_ass_contract_has_karaoke_safe_zone_and_two_line_cues() -> None:
    alignment = _sample_alignment()
    style = AssStyle()
    cues = build_cues(alignment, style)
    ass = render_ass(alignment, style)

    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass
    assert "WrapStyle: 2" in ass
    assert "Noto Sans SemiBold,113.4" in ass
    assert "&H009AD920,&H00B5D8E7" in ass
    assert "&H00101010,&HFF000000,-1,0,0,0,100,100,0,0,1,6,0,2" in ass
    assert ",72,72,340,1" in ass
    assert "{\\k" in ass
    assert "{\\kf" not in ass
    assert "\\N" in ass
    assert "Lektion" in ass
    assert "Nektion" not in ass
    assert len(cues) == 18
    assert all(1 <= len(cue) <= 6 for cue in cues)
    assert ass.count("Dialogue: 0,") == len(cues)
    assert all(line.count("\\N") == 1 for line in ass.splitlines() if line.startswith("Dialogue"))
    assert "\\N{\\alpha&HFF&}." in ass
    long_four_word_cue = next(line for line in ass.splitlines() if "Nachmittag" in line)
    assert "Nachmittag {\\k" in long_four_word_cue
    assert "\\N" in long_four_word_cue


@pytest.mark.parametrize(
    ("words", "has_hidden_row"),
    [
        (("Hallo", "Anna."), True),
        (("Heute", "lernen", "wir", "zusammen", "Deutsch."), False),
    ],
)
def test_karaoke_layout_never_exceeds_two_fixed_rows(
    words: tuple[str, ...], has_hidden_row: bool
) -> None:
    timings = tuple(
        WordTiming(word, float(index), float(index + 1))
        for index, word in enumerate(words)
    )
    ass = render_ass(AlignmentResult(words=timings, score=100.0))
    dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue"))
    assert dialogue.count("\\N") == 1
    assert ("\\N{\\alpha&HFF&}." in dialogue) is has_hidden_row


def test_karaoke_cues_never_overlap_after_early_start_offset() -> None:
    ass = render_ass(_sample_alignment())

    def to_seconds(value: str) -> float:
        hours, minutes, seconds = value.split(":")
        whole, centiseconds = seconds.split(".")
        return int(hours) * 3600 + int(minutes) * 60 + int(whole) + int(centiseconds) / 100

    intervals = []
    for line in ass.splitlines():
        if line.startswith("Dialogue: "):
            fields = line.split(",", 9)
            intervals.append((to_seconds(fields[1]), to_seconds(fields[2])))

    assert intervals
    assert all(
        end <= next_start
        for (_, end), (next_start, _) in pairwise(intervals)
    )


def test_karaoke_cues_preserve_alignment_words_and_durations() -> None:
    alignment = _sample_alignment()
    style = AssStyle()
    cues = build_cues(alignment, style)
    dialogue = [
        line
        for line in render_ass(alignment, style).splitlines()
        if line.startswith("Dialogue: ")
    ]

    assert len(dialogue) == len(cues)
    for cue, line in zip(cues, dialogue, strict=True):
        payload = line.split(",", 9)[9]
        fragments = re.findall(r"\{\\k(\d+)\}([^{}]+)", payload)
        rendered_words = tuple(fragment.replace("\\N", "").strip() for _, fragment in fragments)
        assert rendered_words == tuple(word.word for word in cue)

        actual_durations = [int(duration) for duration, _ in fragments]
        expected_durations = []
        cue_end = cue[-1].end_seconds
        for index, word in enumerate(cue):
            next_start = cue[index + 1].start_seconds if index + 1 < len(cue) else cue_end
            expected_durations.append(
                max(1, round((max(next_start, word.end_seconds) - word.start_seconds) * 100))
            )
        assert actual_durations == expected_durations


def test_learning_timeline_slows_speech_and_adds_sentence_practice_pauses() -> None:
    alignment = _sample_alignment()
    pace = LearningPace()

    timeline = build_learning_timeline(
        alignment,
        source_duration_seconds=37.056,
        pace=pace,
    )

    assert pace.playback_rate == 0.95
    assert pace.pause_after_sentence_seconds == 0.10
    assert timeline.sentence_count == 8
    assert timeline.output_duration_seconds == pytest.approx(39.906316, abs=0.001)
    assert timeline.alignment.words[0].start_seconds == pytest.approx(0.20 + 0.19 / 0.95)
    assert timeline.alignment.words[-1].end_seconds == pytest.approx(
        0.20 + 36.84 / 0.95 + 0.7
    )
    sentence_two = next(word for word in timeline.alignment.words if word.word == "Ich")
    assert sentence_two.start_seconds == pytest.approx(0.20 + 4.92 / 0.95 + 0.1)

    filter_graph = audio_module._filter_graph(timeline, pace)
    assert "atrim=duration=0.200000[intro]" in filter_graph
    assert "atempo=0.950000" in filter_graph
    assert "atrim=duration=0.100000[pause0]" in filter_graph
    assert "[intro][spoken0]" in filter_graph

    ass = render_ass(timeline.alignment)
    first_cue = next(line for line in ass.splitlines() if line.startswith("Dialogue: 0,"))
    assert first_cue.startswith("Dialogue: 0,0:00:00.25,")
    first_sentence_last_cue = next(line for line in ass.splitlines() if "Nachmittag" in line)
    assert first_sentence_last_cue.startswith("Dialogue: 0,0:00:02.30,0:00:05.33,")


def test_whisper_response_parser_rejects_non_monotonic_words() -> None:
    payload = {
        "words": [
            {"word": "eins", "start": 1.0, "end": 1.5},
            {"word": "zwei", "start": 0.0, "end": 0.5},
        ]
    }

    with pytest.raises(WhisperResponseError, match="not monotonic"):
        parse_word_timestamps(payload)


def test_saved_failure_fragment_expands_start_equal_to_end() -> None:
    # Це лише точний збережений фрагмент старої помилки start == end.
    words = parse_word_timestamps(
        {
            "words": [
                {
                    "word": "kurz",
                    "start": 25.3799991607666,
                    "end": 25.3799991607666,
                }
            ]
        }
    )

    assert words[0].start_seconds == 25.3799991607666
    assert words[0].end_seconds == pytest.approx(25.3899991607666)


def test_whisper_response_parser_rejects_reversed_interval() -> None:
    with pytest.raises(WhisperResponseError, match="Invalid interval"):
        parse_word_timestamps(
            {"words": [{"word": "falsch", "start": 2.0, "end": 1.99}]}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ("de", "en", "uk", "pl", "nl"))
async def test_whisper_calls_provider_for_each_strategy_without_cache(
    tmp_path: Path,
    language: str,
) -> None:
    class _TranscriptionsEndpoint:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def create(self, **payload: object) -> SimpleNamespace:
            self.calls.append(payload)
            return SimpleNamespace(
                words=[SimpleNamespace(word="Hallo", start=0.0, end=0.4)]
            )

    endpoint = _TranscriptionsEndpoint()
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=endpoint))
    transcriber = OpenAIWhisperWordTranscriber(
        api_key="test-key",
        language=language,
        client=client,
    )
    audio = tmp_path / "source.mp3"
    audio.write_bytes(b"audio")

    first = await transcriber.transcribe(audio, strategy="word")
    second = await transcriber.transcribe(audio, strategy="sentence")

    assert first == second == (WordTiming("Hallo", 0.0, 0.4),)
    assert [call["temperature"] for call in endpoint.calls] == [0.0, 0.2]
    assert all(call["response_format"] == "verbose_json" for call in endpoint.calls)
    assert all(call["language"] == language for call in endpoint.calls)
    assert list(tmp_path.iterdir()) == [audio]


@pytest.mark.asyncio
async def test_ready_audio_encoder_preserves_mp3_command_and_publishes_non_empty_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "paced.wav"
    source.write_bytes(b"paced-audio")
    destination = tmp_path / "nested" / "ready.mp3"
    captured: dict[str, object] = {}

    def _run(command: list[str], **kwargs: object) -> SimpleNamespace:
        captured["command"] = command
        captured["kwargs"] = kwargs
        assert destination.parent.is_dir()
        destination.write_bytes(b"ready-mp3")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(audio_module.subprocess, "run", _run)

    result = await FFmpegReadyAudioEncoder().encode(
        source=source,
        destination=destination,
    )

    assert result == destination
    assert destination.read_bytes() == b"ready-mp3"
    assert captured["command"] == [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source.resolve()),
        "-map_metadata",
        "-1",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "256k",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(destination.resolve()),
    ]
    assert captured["kwargs"] == {
        "capture_output": True,
        "text": True,
        "timeout": 300,
        "check": False,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


def test_all_media_subprocess_calls_suppress_windows_console_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    audio_calls: list[dict[str, object]] = []

    def run_audio(_command: list[str], **kwargs: object) -> SimpleNamespace:
        audio_calls.append(kwargs)
        return SimpleNamespace(returncode=0, stdout="1.0\n", stderr="")

    monkeypatch.setattr(audio_module.subprocess, "run", run_audio)
    audio_module._run(["ffprobe"])
    audio_module._silence_intervals("ffmpeg", source)

    assert len(audio_calls) == 2
    assert all(call["creationflags"] == expected_flags for call in audio_calls)

    video_calls: list[dict[str, object]] = []

    def run_video(_command: list[str], **kwargs: object) -> SimpleNamespace:
        video_calls.append(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout="{}",
            stderr="n_samples: 1024\nmean_volume: -20.0 dB\nmax_volume: -1.0 dB\n",
        )

    monkeypatch.setattr(video_module.subprocess, "run", run_video)
    video_module._run(["ffprobe"])
    video_module._decoded_audio_levels("ffmpeg", source)

    assert len(video_calls) == 2
    assert all(call["creationflags"] == expected_flags for call in video_calls)


@pytest.mark.parametrize(
    ("returncode", "message"),
    [(1, "encoding failed"), (0, "missing or empty")],
)
@pytest.mark.asyncio
async def test_ready_audio_encoder_uses_adapter_error_for_ffmpeg_or_empty_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    message: str,
) -> None:
    monkeypatch.setattr(
        audio_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=returncode, stderr="failed"),
    )

    with pytest.raises(ReadyAudioEncodingError, match=message):
        await FFmpegReadyAudioEncoder().encode(
            source=tmp_path / "paced.wav",
            destination=tmp_path / "ready.mp3",
        )


@pytest.mark.parametrize("karaoke_enabled", (True, False))
def test_subtitle_renderer_respects_karaoke_switch(tmp_path, karaoke_enabled):
    import asyncio

    from anna_backend.subtitles.ass import AssSubtitleRenderer

    alignment = _sample_alignment()
    destination = tmp_path / "karaoke.ass"
    renderer = AssSubtitleRenderer(karaoke_enabled=karaoke_enabled)
    asyncio.run(renderer.render(alignment, destination))
    content = destination.read_text(encoding="utf-8-sig")
    assert "[Script Info]" in content
    assert "[Events]" in content
    assert ("Dialogue:" in content) is karaoke_enabled
    assert ("{\\k" in content) is karaoke_enabled
    if karaoke_enabled:
        assert content == render_ass(alignment)
