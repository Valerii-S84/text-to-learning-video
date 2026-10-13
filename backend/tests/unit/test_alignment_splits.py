import pytest

from anna_backend.alignment.aligner import (
    AlignmentError,
    align_spoken_text,
    format_alignment_diagnostics,
)
from anna_backend.media_types import WordTiming


def timings(text):
    return tuple(
        WordTiming(word, index * 0.2, (index + 1) * 0.2)
        for index, word in enumerate(text.split())
    )


@pytest.mark.parametrize(
    ("approved", "heard"),
    [
        ("будь-якою", "будь якою"),
        ("вдень,", "в день"),
        ("будь-якою", "будь я ко ю"),
        ("ВДЕНЬ", "в день"),
    ],
)
def test_exact_splits_preserve_spelling_and_complete_timing(approved, heard):
    transcript = timings(f"А {heard} далі")
    text = f"А {approved} далі"
    result = align_spoken_text(text, transcript)
    assert result.score == 100.0
    assert result.words == (
        transcript[0],
        WordTiming(approved, transcript[1].start_seconds, transcript[-2].end_seconds),
        transcript[-1],
    )
    assert "mismatches=[none]" in format_alignment_diagnostics(text, transcript)


@pytest.mark.parametrize(
    ("approved", "heard"),
    [
        ("будь-якою", "будь іншою"),
        ("вдень", "в тінь"),
        ("вдень", "день"),
        ("вдень", "в цей день"),
        ("вдень", "в день зайве"),
        ("вдень", "день в"),
    ],
)
def test_real_differences_are_still_rejected(approved, heard):
    with pytest.raises(AlignmentError, match="below"):
        align_spoken_text(approved, timings(heard))


def test_fuzzy_split_word_is_accepted_with_approved_spelling_and_combined_timing():
    approved = (
        "Трав’яний чай теж може піднімати тиск Солодка в його складі при тривалому "
        "чи надмірному вживанні може також порушувати серцевий ритм Маєш "
        "гіпертонію Не пий такі збори без узгодження з лікарем"
    )
    heard = (
        "Крав ямий чай теж може піднімати тиск Солодка в його складі при тривалому "
        "чи надмірному вживанні може також порушувати серцевий ритм Маєш "
        "гіпертонію Не пий такі збори без узгодження з лікарем"
    )
    transcript = timings(heard)

    result = align_spoken_text(approved, transcript)

    assert result.score >= 97.0
    assert [word.word for word in result.words] == approved.split()
    assert result.words[0].start_seconds == transcript[0].start_seconds
    assert result.words[0].end_seconds == transcript[1].end_seconds


SLEEP_TEXT = (
    "На дивані засинаєте, а в ліжку раптом починаєте рахувати години до будильника? "
    "Не намагайтеся будь-якою ціною лежати до ранку. "
    "Якщо сон не приходить і ви вже дратуєтеся, встаньте. "
    "Перейдіть у спокійне місце, приглушіть світло, "
    "почитайте кілька сторінок паперової книжки. "
    "Поверніться в ліжко, коли знову почнуть важчати повіки. "
    "Так ліжко поступово перестає бути місцем для хвилювання й знову стає сигналом для сну. "
    "Якщо проблема повторюється місяцями та заважає жити вдень, потрібна допомога фахівця."
)

# Word-strategy transcript from generation-i3ar1wa5.log; timings below are synthetic.
SLEEP_TRANSCRIPT = (
    "На дивані засинаєте а в ліжку раптом починаєте рахувати години до будильника "
    "Не намагайтеся будь якою ціною лежати до ранку "
    "Якщо сон не приходить і ви вже дратуєтеся встаньте "
    "Перейдіть у спокійне місце приглушіть світло почитайте кілька сторінок паперової книжки "
    "Поверніться в ліжко коли знову почнуть важчати повіки "
    "Так ліжко поступово перестає бути місцем для хвилювання і знову стає сигналом для сну "
    "Якщо проблема повторюється місяцями та заважає жити в день потрібна допомога фахівця"
)


@pytest.mark.parametrize("strategy", ["word", "sentence"])
def test_observed_sleep_transcripts_pass_without_hiding_substitution(strategy):
    heard = SLEEP_TRANSCRIPT
    if strategy == "sentence":
        heard = heard.replace("важчати", "ващати")
    transcript = timings(heard)
    result = align_spoken_text(SLEEP_TEXT, transcript)
    assert [word.word for word in result.words] == SLEEP_TEXT.split()
    for approved, first_part, last_part in [
        ("будь-якою", "будь", "якою"),
        ("вдень,", "в", "день"),
    ]:
        last_index = heard.split().index(last_part)
        assert transcript[last_index - 1].word == first_part
        aligned = next(word for word in result.words if word.word == approved)
        assert aligned.start_seconds == transcript[last_index - 1].start_seconds
        assert aligned.end_seconds == transcript[last_index].end_seconds
    report = format_alignment_diagnostics(SLEEP_TEXT, transcript)
    assert "<extra>" not in report
    if strategy == "word":
        assert result.score == 100.0
        assert "mismatches=[none]" in report
    else:
        assert 97.0 <= result.score < 100.0
        assert "expected='важчати' heard='ващати'" in report
