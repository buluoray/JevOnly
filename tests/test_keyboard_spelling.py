import pytest

from jevonly.core.keyboard import next_word, render_typed, spelling_hint


@pytest.mark.parametrize(
    ("typed", "cursor", "selection_end", "rendered"),
    [
        ("", None, None, "|"),
        ("Boston", None, None, "Boston|"),
        ("Boston", 3, None, "Bos|ton"),
        ("Boston", 1, 4, "B[ost]on"),
        ("Boston", 99, None, "Boston|"),
    ],
)
def test_render_typed_marks_caret_and_selection(typed, cursor, selection_end, rendered):
    assert render_typed(typed, cursor, selection_end) == rendered


@pytest.mark.parametrize(
    ("remaining", "chunk"),
    [
        ("Boston downtown", "Boston "),
        (" downtown", " downtown"),
        ("Boston", "Boston"),
        ("", ""),
    ],
)
def test_next_word_keeps_the_spacing_needed_for_incremental_typing(remaining, chunk):
    assert next_word(remaining) == chunk


def test_spelling_hint_reports_the_untyped_suffix():
    assert spelling_hint("Bos", 3, 3, "Boston") == {
        "remaining_to_type": "ton",
        "field_matches_value_so_far": True,
    }
    assert spelling_hint("Boston", 6, 6, "Boston") == {
        "remaining_to_type": "",
        "field_matches_value_so_far": True,
    }


def test_spelling_hint_removes_selected_text_before_comparing():
    assert spelling_hint("Bosx", 3, 4, "Boston") == {
        "remaining_to_type": "ton",
        "field_matches_value_so_far": True,
    }


def test_spelling_hint_exposes_a_mismatch_that_requires_backspace():
    hint = spelling_hint("Bosx", 4, 4, "Boston")

    assert hint["field_matches_value_so_far"] is False
    assert hint["matching_prefix"] == "Bos"
    assert "1 character(s) after that are wrong" in hint["mismatch"]


def test_spelling_hint_without_a_target_has_no_arithmetic_claims():
    assert spelling_hint("anything", 8, 8, None) == {}
