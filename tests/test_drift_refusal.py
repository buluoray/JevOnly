"""A pre-dispatch drift refusal ("stale candidate: ...") is not a failed action: nothing was sent, so the loop
verifies nothing, blames the control for nothing, and simply looks again."""

from __future__ import annotations

from fake_jev import FakeJev  # noqa: E402
from jevonly import envs
from jevonly.core import keyboard as keyboard_module
from jevonly.core import loop as loop_module
from jevonly.core.loop import run_task


class DriftEnv:
    """The button says 'One way' when observed; the first click finds it re-labelled and refuses; the second
    observation shows the new label and the click lands."""

    def __init__(self, task):
        self.task = task
        self.label = "One way"
        self.acts = 0
        self.clicked = False
        self.last_action_error = None

    def candidates(self):
        return [
            {
                "id": "b0",
                "idx": 0,
                "desc": f'button "{self.label}"',
                "kind": "click",
                "target_key": f"button:{self.label}",
                "side_effect": "reversible",
                "sig": f"button||{self.label}|||||",
            }
        ]

    def observe(self):
        return {
            "url": "https://travel.example/",
            "title": "Trip type",
            "elements": [{"role": "button", "name": self.label}],
            "visible_text": f"{self.label} {'chosen' if self.clicked else ''}",
            "headings": [],
        }

    def act(self, cand, value=None, kind=None):
        self.acts += 1
        if self.acts == 1:
            self.label = "Round trip"  # the page re-rendered under the ballot
            self.last_action_error = (
                'stale candidate: target changed since observation (was "One way", now "Round trip")'
            )
            return
        self.clicked = True

    def undo(self):
        pass

    def fingerprint(self, obs):
        return obs["visible_text"]

    def terminal(self, obs):
        return self.clicked

    def acceptance(self, obs):
        return []

    def close(self):
        pass


def _responder(state, questions, tag):
    out = {}
    for name, q in questions.items():
        if q["type"] == "noul":
            out[name] = {
                "noul": 0.05
                if name in ("offpath", "done") or tag == "risk" or name.startswith(("value_", "needs_"))
                else 0.95
            }
            continue
        crit = q["criteria"]
        choice = next((o for o in crit if o == "b0"), None) if name == "target" else None
        choice = choice or ("succeeded" if name == "result" else next(iter(crit)))
        rest = 0.05 / max(1, len(crit) - 1)
        out[name] = {"choice": choice, "probabilities": {o: 0.95 if o == choice else rest for o in crit}}
    return out


def test_drift_refusal_reobserves_without_verifying_or_blaming(monkeypatch):
    fake = FakeJev(responder=_responder)
    monkeypatch.setattr(loop_module, "jev", fake)
    monkeypatch.setattr(keyboard_module, "jev", fake, raising=False)
    monkeypatch.setitem(envs._REGISTRY, "drift", DriftEnv)
    task = {
        "id": "drift",
        "env": "drift",
        "goal": "Choose the trip type and stop",
        "start": "https://travel.example/",
        "facts": {},
        "terminal": {},
        "faults": {},
        "max_steps": 2,  # refusal (free) + click; the terminal check runs on the next look
        "irreversible": "refuse",
    }
    notes = []
    log = run_task(task, on_event=lambda k, p: notes.append(p.get("text", "")) if k == "note" else None)
    assert log["success"], log
    assert log["drift_refusals"] == 1
    assert log["steps"][0]["free_look"] is True and log["steps"][0]["drift_refused"] is True
    # the refused step verified nothing: the first plan is followed by a plan, not by a verify
    tags = [c["tag"] for c in fake.calls]
    first_plan = tags.index("judge+next")
    assert tags[first_plan + 1] == "judge+next", tags
    assert any(
        n.startswith("stale candidate: target changed") and n.endswith("-> re-observe and re-plan") for n in notes
    )
    # the control was not counted against: the same target was clicked on the very next step
    assert log["steps"][1]["events"][-1]["outcome"].startswith("accepted"), log["steps"][1]["events"]
