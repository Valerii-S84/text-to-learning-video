from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from anna_backend.media_types import AlignmentResult, WordTiming


@dataclass(frozen=True, slots=True)
class AssStyle:
    play_res_x: int = 1080
    play_res_y: int = 1920
    font_name: str = "Noto Sans SemiBold"
    font_size: float = 113.4
    margin_left: int = 72
    margin_right: int = 72
    margin_vertical: int = 340
    active_colour: str = "&H009AD920"
    base_colour: str = "&H00B5D8E7"
    outline_colour: str = "&H00101010"
    back_colour: str = "&HFF000000"
    border_style: int = 1
    outline_size: int = 6
    min_words_per_cue: int = 4
    max_words_per_cue: int = 6
    max_characters_per_line: int = 19
    karaoke_offset_seconds: float = -0.15

    def __post_init__(self) -> None:
        if not -2.0 <= self.karaoke_offset_seconds <= 2.0:
            raise ValueError("karaoke_offset_seconds must be between -2 and 2")


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    whole_seconds, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{fraction:02d}"


def _escape_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _sentence_groups(words: tuple[WordTiming, ...]) -> list[list[WordTiming]]:
    groups: list[list[WordTiming]] = []
    current: list[WordTiming] = []
    for word in words:
        current.append(word)
        if re.search(r"[.!?…][\"'»”)]*$", word.word):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _balanced_chunks(words: list[WordTiming], style: AssStyle) -> list[list[WordTiming]]:
    if len(words) <= style.max_words_per_cue:
        return [words]
    chunk_count = math.ceil(len(words) / style.max_words_per_cue)
    base, extra = divmod(len(words), chunk_count)
    sizes = [base + (1 if index < extra else 0) for index in range(chunk_count)]
    chunks: list[list[WordTiming]] = []
    cursor = 0
    for size in sizes:
        chunks.append(words[cursor : cursor + size])
        cursor += size
    return chunks


def build_cues(alignment: AlignmentResult, style: AssStyle) -> list[list[WordTiming]]:
    cues: list[list[WordTiming]] = []
    for sentence in _sentence_groups(alignment.words):
        cues.extend(_balanced_chunks(sentence, style))
    if any(len(cue) > style.max_words_per_cue for cue in cues):
        raise ValueError("ASS cue exceeds max_words_per_cue")
    return cues


def _line_break_index(words: list[WordTiming], style: AssStyle) -> int | None:
    if len(words) < 2:
        return None
    lengths = [len(item.word) for item in words]
    total = sum(lengths) + len(words) - 1
    if len(words) < 5 and total <= style.max_characters_per_line:
        return None
    running = 0
    candidates: list[tuple[int, int]] = []
    for index, length in enumerate(lengths[:-1], start=1):
        running += length + (1 if index > 1 else 0)
        candidates.append((abs(total - 2 * running), index))
    return min(candidates)[1]


def _karaoke_text(words: list[WordTiming], style: AssStyle) -> str:
    line_break = _line_break_index(words, style)
    cue_end = words[-1].end_seconds
    fragments: list[str] = []
    for index, word in enumerate(words):
        next_start = words[index + 1].start_seconds if index + 1 < len(words) else cue_end
        duration = max(
            1,
            round((max(next_start, word.end_seconds) - word.start_seconds) * 100),
        )
        separator = ""
        if index:
            separator = "\\N" if line_break == index else " "
        fragments.append(f"{separator}{{\\k{duration}}}{_escape_text(word.word)}")
    if line_break is None:
        fragments.append("\\N{\\alpha&HFF&}.")
    return "".join(fragments)


def render_ass(
    alignment: AlignmentResult,
    style: AssStyle | None = None,
    *,
    karaoke_enabled: bool = True,
) -> str:
    profile = style or AssStyle()
    cues = build_cues(alignment, profile) if karaoke_enabled else []
    style_format = (
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
    )
    style_line = (
        f"Style: Karaoke,{profile.font_name},{profile.font_size},"
        f"{profile.active_colour},{profile.base_colour},"
        f"{profile.outline_colour},{profile.back_colour},"
        "-1,0,0,0,100,100,0,0,"
        f"{profile.border_style},{profile.outline_size},0,2,"
        f"{profile.margin_left},{profile.margin_right},{profile.margin_vertical},1"
    )
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {profile.play_res_x}
PlayResY: {profile.play_res_y}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
{style_format}
{style_line}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dialogue: list[str] = []
    for index, cue in enumerate(cues):
        event_start = max(0.0, cue[0].start_seconds + profile.karaoke_offset_seconds)
        event_end = cue[-1].end_seconds
        if index + 1 < len(cues):
            # The next cue starts slightly early to make the first syllable
            # feel responsive. Do not keep the previous cue alive until the
            # raw word start: that briefly renders two two-row cues at once.
            next_event_start = max(
                0.0,
                cues[index + 1][0].start_seconds + profile.karaoke_offset_seconds,
            )
            event_end = min(event_end, next_event_start)
        if event_end <= event_start:
            raise ValueError("ASS cue timing overlaps or has zero duration")
        dialogue.append(
            "Dialogue: 0,"
            f"{_ass_time(event_start)},{_ass_time(event_end)},"
            f"Karaoke,,0,0,0,,{_karaoke_text(cue, profile)}"
        )
    return header + "\n".join(dialogue) + "\n"


class AssSubtitleRenderer:
    def __init__(
        self, style: AssStyle | None = None, *, karaoke_enabled: bool = True,
    ) -> None:
        self.style = style or AssStyle()
        self.karaoke_enabled = karaoke_enabled

    async def render(self, alignment: AlignmentResult, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            render_ass(alignment, self.style, karaoke_enabled=self.karaoke_enabled),
            encoding="utf-8-sig",
        )
        return destination
