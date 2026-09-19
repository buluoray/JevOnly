import pytest

from jevonly.core.questions import (
    q_bind,
    q_collapse,
    q_commit,
    q_copy_target,
    q_key,
    q_next,
    q_option,
    q_spell_step,
    q_value,
)

CANDIDATE = {
    "id": "field",
    "desc": 'textbox "Destination"',
    "options": ["Boston", "Chicago"],
}


@pytest.mark.parametrize(
    ("name", "question", "expected_name"),
    [
        ("q_next", q_next([CANDIDATE]), "target"),
        ("q_bind", q_bind(CANDIDATE, {"city": "Boston"}), "fact"),
        ("q_option", q_option(CANDIDATE), "option"),
        ("q_commit", q_commit(CANDIDATE), "commit"),
        ("q_collapse", q_collapse([["Boston"], ["Chicago"]], "for the destination"), "pick"),
        ("q_copy_target", q_copy_target(["find the fare", "record the time"]), "wanted"),
        ("q_value", q_value(CANDIDATE, ["Boston", "Chicago"]), "value"),
        ("q_spell_step", q_spell_step(CANDIDATE, "Boston downtown"), "spell"),
        ("q_key", q_key(CANDIDATE, "Bos|", [], "Boston"), "key"),
    ],
)
def test_question_builders_return_the_closed_choice_contract(name, question, expected_name):
    assert list(question) == [expected_name], name
    body = question[expected_name]
    assert set(body) == {"type", "instructions", "criteria"}
    assert body["type"] in {"choice", "noul"}
    assert isinstance(body["instructions"], str) and body["instructions"].strip()
    assert isinstance(body["criteria"], dict) and body["criteria"]
    assert all(isinstance(key, str) and key for key in body["criteria"])
    assert all(isinstance(value, str) and value for value in body["criteria"].values())


@pytest.mark.parametrize(
    "question",
    [
        q_next([CANDIDATE]),
        q_bind(CANDIDATE, {"city": "Boston"}),
        q_collapse([["Boston"], ["Chicago"]], "for the destination"),
        q_collapse(["Boston", "Chicago"], "for the destination", final=True),
        q_copy_target(["find the fare", "record the time"]),
        q_value(CANDIDATE, ["Boston", "Chicago"]),
    ],
)
def test_builders_with_a_skip_path_offer_none(question):
    body = next(iter(question.values()))
    assert "none" in body["criteria"]
