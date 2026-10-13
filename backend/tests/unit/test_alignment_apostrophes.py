import pytest

from anna_backend.alignment.aligner import (
    AlignmentError,
    align_spoken_text,
    format_alignment_diagnostics,
)
from anna_backend.media_types import WordTiming


@pytest.mark.parametrize("apostrophe", ["’", "'", "ʼ"])
def test_apostrophe_splits_keep_approved_words_and_full_timings(apostrophe):
    text = f"м{apostrophe}якістю м{apostrophe}якуш з{apostrophe}їсти,"
    transcript = tuple(
        WordTiming(word, index * 0.2, (index + 1) * 0.2)
        for index, word in enumerate(["м", "якістю", "м", "якуш", "з", "їсти"])
    )
    result = align_spoken_text(text, transcript)
    assert result.score == 100.0
    assert [word.word for word in result.words] == text.split()
    for index, word in enumerate(result.words):
        assert word.start_seconds == pytest.approx(index * 0.4)
        assert word.end_seconds == pytest.approx((index + 1) * 0.4)
    assert "mismatches=[none]" in format_alignment_diagnostics(text, transcript)


def test_separate_words_are_not_merged():
    transcript = (WordTiming("з", 0.0, 0.2), WordTiming("їсти", 0.2, 0.5))
    result = align_spoken_text("з їсти", transcript)
    assert result.score == 100.0
    assert result.words == transcript


def test_inexact_apostrophe_parts_still_fail():
    transcript = (WordTiming("м", 0.0, 0.2), WordTiming("інше", 0.2, 0.5))
    with pytest.raises(AlignmentError, match="below"):
        align_spoken_text("м’якуш", transcript)


def test_unsplit_apostrophe_word_keeps_its_timing():
    result = align_spoken_text("м’якуш", (WordTiming("м'якуш", 0.3, 0.8),))
    assert result.score == 100.0
    assert result.words == (WordTiming("м’якуш", 0.3, 0.8),)


def test_observed_ukrainian_transcript_passes_without_hiding_substitution():
    text = (
        "Кладете хліб у холодильник щоб довше був свіжим Саме там він може "
        "швидше стати черствим Холод стримує плісняву але з м’якістю інша "
        "історія У холодильнику крохмаль швидше перебудовується і м’якуш "
        "твердішає Навіть у закритому пакеті Якщо звичайний хліб без начинки не "
        "встигаєте з’їсти допоможе морозилка Наріжте його поки він свіжий "
        "Розкладіть порціями на один сніданок у пакети для заморожування І "
        "щільно закрийте Діставайте лише потрібну порцію Окремі скибки можна "
        "розморозити і підсмажити в тостері Зручний момент одразу після покупки "
        "Частину на найближчі прийоми їжі решту в морозилку Тоді не доведеться "
        "поспішати з бутербродами щоб урятувати всю хлібину"
    )
    heard = (
        "Кладете хліб у холодильник щоб довше був свіжим Саме там він може "
        "швидше стати черствим Холод стримує плісняву але з м якістю інша "
        "історія У холодильнику крохмаль швидше перебудовується і м якуш "
        "твердішає Навіть у закритому пакеті Якщо звичайний хліб без начинки не "
        "встигаєте з їсти допоможе морозилка Наріжте його поки він свіжий "
        "Розкладіть порціями на один сніданок у пакети для заморожування І "
        "щільно закрийте Діставайте лише потрібну порцію Окремі скибки можна "
        "розморозити і подсмажити в тостері Зручний момент одразу після покупки "
        "Частину на найближчі прийоми їжі решту в морозилку Тоді не доведеться "
        "поспішати з бутербродами щоб урятувати всю хлібину"
    )
    transcript = tuple(
        WordTiming(word, index * 0.2, (index + 1) * 0.2) for index, word in enumerate(heard.split())
    )
    result = align_spoken_text(text, transcript)
    assert 97.0 <= result.score < 100.0
    assert [word.word for word in result.words] == text.split()
    report = format_alignment_diagnostics(text, transcript)
    assert "expected='підсмажити' heard='подсмажити'" in report
    assert "<extra>" not in report
