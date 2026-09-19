"""After a copy, the loop asks which other clause this page can still answer and copies for it next."""

from __future__ import annotations

import pytest
from tests.fake_jev import FakeJev

from jevonly import envs
from jevonly.core import keyboard as keyboard_module
from jevonly.core import loop as loop_module
from jevonly.core.loop import run_task

LINE = "6:20 PM - 7:05 AM Air France Nonstop $1,634 Select flight"


class ResultsEnv:
    def __init__(self, task):
        self.task = task

    def candidates(self):
        return [
            {
                "id": "details",
                "idx": 0,
                "desc": 'button "Flight details"',
                "kind": "click",
                "target_key": "button:Flight details",
                "side_effect": "unknown",
            },
            {
                "id": "copy",
                "idx": -1,
                "kind": "copy",
                "options": None,
                "target_key": "copy",
                "needs_commit": False,
                "side_effect": "reversible",
                "fam": "copy",
                "desc": "copy a value shown on this page -- text a later step needs; changes nothing on the page",
            },
        ]

    def observe(self):
        return {
            "url": "https://flights.example/results",
            "title": "Results",
            "elements": [{"role": "button", "name": "Flight details", "context": LINE}],
            "visible_text": "Results. " + LINE,
            "headings": ["Results"],
        }

    def act(self, cand, value=None, kind=None):
        pass

    def undo(self):
        pass

    def fingerprint(self, obs):
        return obs["visible_text"]

    def keyboard(self, cand=None, key=None, text=None, focus=False):
        return {"typed": "", "cursor": 0, "sel_end": 0, "options": []}

    def inflight(self):
        return 0

    def wait_inflight(self, cap_ms=3000):
        return 0

    def close(self):
        pass

    def terminal(self, obs):
        return False


@pytest.fixture
def results_env():
    envs.register("results", ResultsEnv)
    yield
    envs._REGISTRY.pop("results", None)


def test_second_value_is_copied_right_after_the_first(monkeypatch, results_env, events_sink):
    jev = FakeJev()
    jev.add_rule(question="value_0", noul=0.05)  # "Open the results" is a thing to do
    jev.add_rule(question="value_1", noul=0.9)
    jev.add_rule(question="value_2", noul=0.9)
    jev.add_rule(question="done", noul=0.2)
    jev.add_rule(question="offpath", noul=0.05)
    jev.add_rule(question="target", contains="copy a value")  # every plan: copy
    # the clause question names the time first; once the time is copied the rules below flip to the price
    jev.add_rule(question="wanted", contains="departure time")
    jev.add_rule(question="pick", tag="copy", contains="6:20 PM")
    jev.add_rule(question="ok", noul=0.95)
    jev.add_rule(noul=0.0)
    monkeypatch.setattr(loop_module, "jev", jev)
    monkeypatch.setattr(keyboard_module, "jev", jev)

    # the second collapse must pick the price: switch the pick rule after the first copy lands
    def on_event(kind, payload):
        events_sink(kind, payload)
        if kind == "copy" and payload["text"] == "6:20 PM":
            jev.rules = [r for r in jev.rules if r.get("question") not in ("pick", "wanted")]
            jev.rules.insert(0, {"question": "pick", "tag": "copy", "contains": "$1,634"})
            jev.rules.insert(0, {"question": "wanted", "contains": "price"})
        if kind == "copy" and payload["text"] == "$1,634":
            jev.rules = [r for r in jev.rules if r.get("question") not in ("wanted", "target")]
            jev.rules.insert(0, {"question": "wanted", "choice": "none"})
            jev.rules.insert(0, {"question": "target", "choice": "none"})

    task = {
        "id": "chain",
        "env": "results",
        "goal": "Open the results, note the departure time, then note the price of the first flight",
        "start": "https://flights.example/results",
        "facts": {},
        "terminal": {},
        "faults": {},
        "max_steps": 3,
        "irreversible": "refuse",
    }
    run_task(task, variant="noaccept", on_event=on_event)
    copies = [e for e in events_sink if e["kind"] == "copy"]
    assert [c["text"] for c in copies] == ["6:20 PM", "$1,634"]
    assert [c["step"] for c in copies] == [0, 1], "the price is copied on the very next plan, not after a none streak"
    notes = [e["text"] for e in events_sink if e["kind"] == "note"]
    assert any("copying for it next" in n for n in notes)
    assert any("asks for 2 value(s)" in n for n in notes)
    end = [e for e in events_sink if e["kind"] == "end"][-1]
    assert end["stopped"] == "values_in_hand" and end["success"] is True
    assert end["steps"] == 3, "both values in hand and 'none' leading is the stop -- no none streak needed"
