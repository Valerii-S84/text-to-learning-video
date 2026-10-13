from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from itertools import pairwise

from rapidfuzz.fuzz import ratio

from anna_backend.media_types import AlignmentResult, WordTiming


class AlignmentError(ValueError):
    """Frozen spoken text cannot be safely aligned to the transcription."""


@dataclass(frozen=True, slots=True)
class _Token:
    display: str
    normalized: str


_FUZZY_MERGE_MIN_SIMILARITY = 0.75


def normalize_word(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = "".join(
        character for character in normalized if character.isalnum() and character != "ʼ"
    )
    if normalized in {"і", "й"}:
        return "і"
    return normalized


def tokenize_spoken_text(spoken_text: str) -> tuple[_Token, ...]:
    tokens = tuple(
        _Token(display=value, normalized=normalize_word(value))
        for value in re.findall(r"\S+", spoken_text.strip())
    )
    if not tokens or any(not token.normalized for token in tokens):
        raise AlignmentError("spoken_text must contain non-empty lexical tokens")
    return tokens


def _similarity(left: str, right: str) -> float:
    return ratio(left, right) / 100.0


def _pair_tokens(
    targets: tuple[_Token, ...], transcript: tuple[WordTiming, ...]
) -> tuple[list[tuple[int, ...] | None], float]:
    source = tuple(normalize_word(item.word) for item in transcript)
    if any(not item for item in source):
        raise AlignmentError("transcription contains a word without lexical characters")

    rows = len(targets) + 1
    columns = len(source) + 1
    costs = [[0.0] * columns for _ in range(rows)]
    choices = [[""] * columns for _ in range(rows)]
    for row in range(1, rows):
        costs[row][0] = float(row)
        choices[row][0] = "delete"
    for column in range(1, columns):
        costs[0][column] = float(column)
        choices[0][column] = "insert"

    for row in range(1, rows):
        target = targets[row - 1].normalized
        for column in range(1, columns):
            substitution = costs[row - 1][column - 1] + (
                1.0 - _similarity(targets[row - 1].normalized, source[column - 1])
            )
            deletion = costs[row - 1][column] + 1.0
            insertion = costs[row][column - 1] + 1.0
            costs[row][column], choices[row][column] = min(
                (substitution, "pair"),
                (deletion, "delete"),
                (insertion, "insert"),
                key=lambda item: item[0],
            )
            # Whisper may split one approved word at punctuation or inside it.
            # Merge only adjacent parts whose normalized letters match exactly.
            joined = source[column - 1]
            for part_count in range(2, column + 1):
                if not target.endswith(joined) or len(joined) >= len(target):
                    break
                joined = source[column - part_count] + joined
                if (
                    joined == target
                    and costs[row - 1][column - part_count] < costs[row][column]
                ):
                    costs[row][column] = costs[row - 1][column - part_count]
                    choices[row][column] = f"merge:{part_count}"

            # Whisper can split one spoken word into two slightly inaccurate
            # pieces. Accept this only when the joined letters are close to
            # the approved word, end with the same letter, and the join uses
            # exactly two adjacent transcript words. This keeps true extra
            # words rejected while covering cases such as "Крав ямий" for
            # the approved Ukrainian word "Трав’яний".
            if column >= 2 and len(target) >= 4:
                fuzzy_joined = source[column - 2] + source[column - 1]
                fuzzy_similarity = _similarity(target, fuzzy_joined)
                if (
                    fuzzy_similarity >= _FUZZY_MERGE_MIN_SIMILARITY
                    and fuzzy_joined[-1] == target[-1]
                    and abs(len(fuzzy_joined) - len(target)) <= 2
                    and costs[row - 1][column - 2] < costs[row][column]
                ):
                    costs[row][column] = costs[row - 1][column - 2]
                    choices[row][column] = (
                        f"fuzzy_merge:2:{fuzzy_similarity:.6f}"
                    )

    mapping: list[tuple[int, ...] | None] = [None] * len(targets)
    similarities: list[float] = []
    merged_extra_tokens = 0
    row = len(targets)
    column = len(source)
    while row or column:
        choice = choices[row][column]
        if choice == "pair":
            mapping[row - 1] = (column - 1,)
            similarities.append(_similarity(targets[row - 1].normalized, source[column - 1]))
            row -= 1
            column -= 1
        elif choice.startswith("merge:"):
            count = int(choice.split(":")[1])
            mapping[row - 1] = tuple(range(column - count, column))
            similarities.append(1.0)
            merged_extra_tokens += count - 1
            row -= 1
            column -= count
        elif choice.startswith("fuzzy_merge:"):
            _, count_text, similarity_text = choice.split(":")
            count = int(count_text)
            mapping[row - 1] = tuple(range(column - count, column))
            similarities.append(float(similarity_text))
            merged_extra_tokens += count - 1
            row -= 1
            column -= count
        elif choice == "delete":
            similarities.append(0.0)
            row -= 1
        elif choice == "insert":
            similarities.append(0.0)
            column -= 1
        else:
            raise AlignmentError("internal alignment backtrace failed")

    denominator = max(len(targets), len(source) - merged_extra_tokens)
    score = 100.0 * sum(similarities) / denominator
    return mapping, score


def format_alignment_diagnostics(
    spoken_text: str,
    transcript: tuple[WordTiming, ...],
    *,
    max_mismatches: int = 40,
    max_transcript_chars: int = 4000,
) -> str:
    """Return a bounded report for a rejected Whisper alignment."""

    targets = tokenize_spoken_text(spoken_text)
    source = tuple(normalize_word(item.word) for item in transcript)
    transcript_text = " ".join(item.word for item in transcript)
    if len(transcript_text) > max_transcript_chars:
        transcript_text = transcript_text[:max_transcript_chars] + "..."
    non_lexical = [
        f"index={index} heard='{item.word}'"
        for index, (item, normalized) in enumerate(zip(transcript, source, strict=True))
        if not normalized
    ]
    lexical_transcript = tuple(
        item for item, normalized in zip(transcript, source, strict=True) if normalized
    )
    if not lexical_transcript:
        return (
            f"whisper_transcript='{transcript_text}'; "
            "diagnostic_score=unavailable; "
            f"non_lexical_words=[{'; '.join(non_lexical[:max_mismatches])}]"
        )

    mapping, score = _pair_tokens(targets, lexical_transcript)
    lexical_source = tuple(normalize_word(item.word) for item in lexical_transcript)
    matched_sources: set[int] = set()
    mismatches: list[str] = []

    for target, source_index in zip(targets, mapping, strict=True):
        if source_index is None:
            mismatches.append(f"expected='{target.display}' heard='<missing>'")
            continue
        matched_sources.update(source_index)
        heard_normalized = "".join(lexical_source[index] for index in source_index)
        heard = " ".join(lexical_transcript[index].word for index in source_index)
        if target.normalized != heard_normalized:
            similarity = _similarity(
                target.normalized, heard_normalized
            ) * 100
            mismatches.append(
                f"expected='{target.display}' heard='{heard}' "
                f"similarity={similarity:.1f}%"
            )

    for source_index, item in enumerate(lexical_transcript):
        if source_index not in matched_sources:
            mismatches.append(f"expected='<extra>' heard='{item.word}'")

    mismatch_text = "; ".join(mismatches[:max_mismatches])
    if len(mismatches) > max_mismatches:
        mismatch_text += f"; ... ({len(mismatches) - max_mismatches} more)"
    return (
        f"whisper_transcript='{transcript_text}'; "
        f"ignored_non_lexical_words=[{'; '.join(non_lexical[:max_mismatches])}]; "
        f"diagnostic_score={score:.2f}%; "
        f"mismatches=[{mismatch_text or 'none'}]"
    )


def _interpolate_missing(
    targets: tuple[_Token, ...],
    transcript: tuple[WordTiming, ...],
    mapping: list[tuple[int, ...] | None],
) -> list[WordTiming]:
    aligned: list[WordTiming | None] = [None] * len(targets)
    for index, source_index in enumerate(mapping):
        if source_index is None:
            continue
        first = transcript[source_index[0]]
        last = transcript[source_index[-1]]
        aligned[index] = WordTiming(
            word=targets[index].display,
            start_seconds=first.start_seconds,
            end_seconds=last.end_seconds,
        )

    cursor = 0
    while cursor < len(aligned):
        if aligned[cursor] is not None:
            cursor += 1
            continue
        run_start = cursor
        while cursor < len(aligned) and aligned[cursor] is None:
            cursor += 1
        run_end = cursor
        previous = aligned[run_start - 1] if run_start else None
        following = aligned[run_end] if run_end < len(aligned) else None
        interval_start = previous.end_seconds if previous else max(0.0, transcript[0].start_seconds)
        interval_end = following.start_seconds if following else transcript[-1].end_seconds
        if interval_end <= interval_start:
            raise AlignmentError("no time interval available for missing approved words")
        weights = [max(1, len(targets[index].normalized)) for index in range(run_start, run_end)]
        total_weight = sum(weights)
        if any((interval_end - interval_start) * weight / total_weight < 0.01
               for weight in weights):
            raise AlignmentError("time interval is too short for missing approved words")
        position = interval_start
        for offset, weight in enumerate(weights):
            duration = (interval_end - interval_start) * weight / total_weight
            end = interval_end if offset == len(weights) - 1 else position + duration
            aligned[run_start + offset] = WordTiming(
                word=targets[run_start + offset].display,
                start_seconds=position,
                end_seconds=end,
            )
            position = end

    return [item for item in aligned if item is not None]


def align_spoken_text(
    spoken_text: str,
    transcript: tuple[WordTiming, ...],
    *,
    minimum_score: float = 97.0,
) -> AlignmentResult:
    if not transcript:
        raise AlignmentError("transcription must contain words")
    targets = tokenize_spoken_text(spoken_text)
    lexical_transcript = tuple(
        item for item in transcript if normalize_word(item.word)
    )
    if not lexical_transcript:
        raise AlignmentError("transcription contains no lexical words")
    mapping, score = _pair_tokens(targets, lexical_transcript)
    if score + 1e-9 < minimum_score:
        raise AlignmentError(
            f"alignment score {score:.2f}% is below the required {minimum_score:.2f}%"
        )
    if mapping and mapping[-1] is None:
        raise AlignmentError("alignment does not confirm the end of the approved text")
    words = tuple(_interpolate_missing(targets, lexical_transcript, mapping))
    for previous, current in pairwise(words):
        if current.start_seconds + 0.05 < previous.start_seconds:
            raise AlignmentError("aligned timestamps are not monotonic")
        if current.start_seconds - previous.end_seconds > 2.5:
            raise AlignmentError("aligned words contain a gap larger than 2.5 seconds")
    return AlignmentResult(words=words, score=round(score, 4))


class DeterministicTextAligner:
    def __init__(self, *, minimum_score: float = 97.0) -> None:
        self.minimum_score = minimum_score

    def align(self, spoken_text: str, words: tuple[WordTiming, ...]) -> AlignmentResult:
        return align_spoken_text(spoken_text, words, minimum_score=self.minimum_score)
