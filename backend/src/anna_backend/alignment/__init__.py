"""Deterministic alignment of Whisper timings to frozen spokenText."""

from anna_backend.alignment.aligner import (
    AlignmentError,
    DeterministicTextAligner,
    align_spoken_text,
    normalize_word,
)

__all__ = [
    "AlignmentError",
    "DeterministicTextAligner",
    "align_spoken_text",
    "normalize_word",
]
