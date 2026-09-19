"""Real browser, scripted Jev: a search box whose value is a phrase of the goal that no short span covers.
Jev answers `none` to the span question, narrows the goal from both ends, and the phrase is typed."""

from pathlib import Path

import pytest

import jevonly.core.keyboard as keyboard_module
import jevonly.core.loop as loop_module
from jevonly.core.loop import run_task

pytestmark = pytest.mark.e2e

GOAL = "Open the site, then search for tallest building in Portland on it, and stop when the results show"


def _choice(criteria, choice, probability=0.97):
    rest = (1.0 - probability) / (len(criteria) - 1) if len(criteria) > 1 else 0.0
    return {"choice": choice, "probabilities": {o: probability if o == choice else rest for o in criteria}}


def _responder(state, questions, tag):
    answers = {}
    filled = any(
        "tallest building in Portland" in str(e.get("value", "")) for e in state.get("state", {}).get("elements", [])
    )
    for name, q in questions.items():
        if q["type"] == "noul":
            if name == "offpath" or tag == "risk":
                score = 0.0
            elif name.startswith("value_"):
                score = 0.95 if "search for" in q["instructions"] else 0.05
            else:
                score = 0.95
            answers[name] = {"noul": score}
            continue
        crit = q["criteria"]
        if name == "target":
            wanted = 'button "Search"' if filled else 'textbox "Search query"'
            choice = next((o for o, d in crit.items() if wanted in d), "none")
        elif name == "fact":
            choice = "none"
        elif name == "value":
            choice = "none"  # no short span is the value -> narrow the goal
        elif name == "pick" and "text" in state:
            text = state["text"]
            if text == "tallest building in Portland on it":
                choice = "tail_off_2"
            elif text.startswith("search for "):
                choice = "head_off_2"
            elif text == "tallest building in Portland":
                choice = "all"
            else:
                choice = "none"
        elif name == "pick":
            choice = next((o for o, d in crit.items() if "search for tallest" in d), "none")  # the clause
        elif name == "spell":
            choice = "all"
        else:
            choice = next(iter(crit))
        answers[name] = _choice(crit, choice)
    return answers


@pytest.mark.usefixtures("jev_playwright")
def test_field_value_is_narrowed_from_the_goal_then_typed(monkeypatch, fake_jev, events_sink):
    page = Path(__file__).parent / "pages" / "form.html"
    jev = fake_jev(responder=_responder)
    monkeypatch.setattr(loop_module, "jev", jev)
    monkeypatch.setattr(keyboard_module, "jev", jev)
    task = {
        "id": "narrow-e2e",
        "env": "browser",
        "start": page.resolve().as_uri(),
        "goal": GOAL,
        "facts": {},
        "faults": {},
        "terminal": {"text": "Search submitted for tallest building in Portland"},
        "max_steps": 4,
        "budget_s": 30,
    }
    result = run_task(task, variant="std_kb", on_event=events_sink)
    assert result["success"] is True
    notes = [e["text"] for e in events_sink if e["kind"] == "note"]
    assert any("narrowed the goal down to the value for this field: `tallest building in Portland`" in n for n in notes)
    trace = next(e for e in events_sink if e["kind"] == "copy_trace" and e.get("source") == "goal")
    assert [r["kind"] for r in trace["attempts"][0]["rounds"]] == ["parts", "narrow", "narrow", "narrow"]
    assert any(e["kind"] == "key" and e["choice"] == "tallest building in Portland" for e in events_sink)
