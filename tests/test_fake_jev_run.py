from pathlib import Path

import pytest

import jevonly.core.keyboard as keyboard_module
import jevonly.core.loop as loop_module
from jevonly.core.loop import EVENT_KINDS, run_task

pytestmark = pytest.mark.e2e


def _choice(criteria, choice, probability=0.97):
    remainder = (1.0 - probability) / (len(criteria) - 1) if len(criteria) > 1 else 0.0
    return {
        "choice": choice,
        "probabilities": {option: probability if option == choice else remainder for option in criteria},
    }


def _scripted_form_responder(state, questions, tag):
    answers = {}
    field_filled = any(element.get("value") == "Boston" for element in state.get("state", {}).get("elements", []))

    for name, question in questions.items():
        if question["type"] == "noul":
            score = 0.0 if name in {"offpath", "answers_question"} and tag == "risk" else 0.95
            if name == "offpath":
                score = 0.0
            answers[name] = {"noul": score}
            continue

        criteria = question["criteria"]
        if name == "target":
            wanted = 'button "Search"' if field_filled else 'textbox "Search query"'
            choice = next(option for option, description in criteria.items() if wanted in description)
        elif name == "fact":
            choice = "none"
        elif name == "value":
            choice = "Boston"
        elif name == "spell":
            choice = "all"
        else:
            choice = next(iter(criteria))
        answers[name] = _choice(criteria, choice)
    return answers


@pytest.mark.usefixtures("jev_playwright")
def test_run_task_uses_closed_votes_for_keyboard_and_submit(monkeypatch, fake_jev, events_sink):
    page = Path(__file__).parent / "pages" / "form.html"
    jev = fake_jev(responder=_scripted_form_responder)
    monkeypatch.setattr(loop_module, "jev", jev)
    monkeypatch.setattr(keyboard_module, "jev", jev)
    task = {
        "id": "fake-jev-form",
        "env": "browser",
        "start": page.resolve().as_uri(),
        "goal": "type Boston into the field and press Search",
        "facts": {},
        "faults": {},
        "terminal": {"text": "Search submitted for Boston"},
        "max_steps": 4,
        "budget_s": 30,
    }

    result = run_task(task, variant="std_kb", on_event=events_sink)

    assert result["success"] is True
    assert all(event["kind"] in EVENT_KINDS for event in events_sink)
    kinds = [event["kind"] for event in events_sink]
    keyboard_act = next(
        index
        for index, event in enumerate(events_sink)
        if event["kind"] == "act" and event["action_kind"] == "keyboard"
    )
    keyboard_verify = next(
        index
        for index, event in enumerate(events_sink)
        if index > keyboard_act and event["kind"] == "verify" and event["accepted"]
    )
    assert kinds.index("observe") < kinds.index("plan") < keyboard_act < keyboard_verify
    assert any(event["kind"] == "key" and event["choice"] == "Boston" for event in events_sink)
    assert any(event["kind"] == "act" and 'button "Search"' in event["desc"] for event in events_sink)
    assert events_sink[-1]["kind"] == "end"
    assert events_sink[-1]["success"] is True

    assert jev.calls
    assert all(call["question_names"] and call["criteria"] for call in jev.calls)
