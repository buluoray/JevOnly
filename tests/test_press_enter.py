"""A search box that holds typed text is offered "press Enter to submit" as a CHOICE -- the typing path never
presses Enter on its own, so a search with no suggestion to pick would otherwise have no way forward."""

from __future__ import annotations

from pathlib import Path

import pytest

import jevonly.core.keyboard as keyboard_module
import jevonly.core.loop as loop_module
from jevonly.core.loop import run_task
from jevonly.envs.browser.env import BrowserEnv


def _env_with(candidates, url="https://example.test/"):
    env = BrowserEnv.__new__(BrowserEnv)  # no browser child: candidates() only reads the snapshot
    env.task = {"goal": "search for something", "facts": {}}
    env._snap = {"url": url, "title": "t", "candidates": candidates, "visible_text": "", "text_units": []}
    env._snapopts = {"text_budget": 6000, "ctx_budget": 140}
    env._url_stack = []
    env._finds = {}
    return env


def test_enter_is_offered_only_for_a_filled_search_field():
    cands = [
        {"role": "searchbox", "name": "Search", "tag": "input", "value": "JevOnly", "ctx": ""},
        {"role": "searchbox", "name": "Empty", "tag": "input", "value": "", "ctx": ""},
        {"role": "textbox", "name": "First name", "tag": "input", "value": "Ray", "ctx": ""},
        {"role": "textbox", "name": "Find", "tag": "input", "value": "x", "input_type": "search", "ctx": ""},
        {"role": "combobox", "name": "Where to?", "tag": "input", "value": "Paris", "ctx": ""},
        {"role": "combobox", "name": "Cabin", "tag": "select", "value": "Economy", "options": ["Economy"], "ctx": ""},
    ]
    out = _env_with(cands).candidates()
    enters = [c for c in out if c["kind"] == "press_enter"]
    assert [c["idx"] for c in enters] == [0, 3, 4]  # filled searchbox, type=search, typed combobox
    assert enters[0]["id"].startswith("enter-e") and enters[0]["side_effect"] == "navigation"
    assert 'press Enter in searchbox "Search" to submit what it holds ("JevOnly")' in enters[0]["desc"]
    # never for an empty box, a plain text field (a form's Enter may submit the form) or a <select>
    assert not any(c["kind"] == "press_enter" and c["idx"] in (1, 2, 5) for c in out)


GOAL = "Open the site, then search for tallest building in Portland on it, and stop when the results show"


def _choice(criteria, choice, probability=0.97):
    rest = (1.0 - probability) / (len(criteria) - 1) if len(criteria) > 1 else 0.0
    return {"choice": choice, "probabilities": {o: probability if o == choice else rest for o in criteria}}


def _responder(state, questions, tag):
    """No facts: the keyboard path narrows the goal to `tallest building in Portland` and types it, leaving the
    box without pressing Enter. The next plan then picks the Enter option (there is no button on this page)."""
    answers = {}
    elements = state.get("state", {}).get("elements", [])
    filled = any("tallest building in Portland" in str(e.get("value", "")) for e in elements)
    for name, q in questions.items():
        if q["type"] == "noul":
            if name == "offpath" or tag == "risk" or name.startswith(("value_", "needs_")):
                answers[name] = {"noul": 0.05}
            else:
                answers[name] = {"noul": 0.95}
            continue
        crit = q["criteria"]
        if name == "target":
            wanted = "press Enter in" if filled else '"Search query"'
            choice = next((o for o, d in crit.items() if wanted in d), "none")
        elif name in ("fact", "value") or name.startswith("bind_"):
            choice = "none"
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
            choice = next((o for o, d in crit.items() if "search for tallest" in d), "none")
        elif name == "spell":
            choice = "all"
        elif name == "result":
            choice = "succeeded"
        else:
            choice = next(iter(crit))
        answers[name] = _choice(crit, choice)
    return answers


@pytest.mark.e2e
@pytest.mark.usefixtures("jev_playwright")
def test_enter_submits_a_search_that_has_no_button_to_click(monkeypatch, fake_jev, events_sink):
    page = Path(__file__).parent / "pages" / "search_enter.html"
    jev = fake_jev(responder=_responder)
    monkeypatch.setattr(loop_module, "jev", jev)
    monkeypatch.setattr(keyboard_module, "jev", jev)
    task = {
        "id": "enter-e2e",
        "env": "browser",
        "start": page.resolve().as_uri(),
        "goal": GOAL,
        "facts": {},
        "faults": {},
        "terminal": {"text": "Search submitted for tallest building in Portland"},
        "max_steps": 4,
        "budget_s": 30,
    }
    # the keyboard variant types word by word and never presses Enter itself -- the gap this option closes
    result = run_task(task, variant="noaccept_kb", on_event=events_sink)
    assert result["success"] is True, result.get("stopped")
    acts = [e for e in events_sink if e["kind"] == "act"]
    assert any(e.get("action_kind") == "press_enter" for e in acts), [e.get("desc") for e in acts]
