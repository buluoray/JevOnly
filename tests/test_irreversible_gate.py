"""The irreversible-action gate, exercised on a fake environment registered through ``jevonly.envs``.

Jev is scripted to pick a "Place order" button and to call it risky. The gate decides what happens:
``refuse`` never performs it, ``ask`` performs it only when the approver says yes, ``allow`` performs it.
"""

from __future__ import annotations

import pytest
from tests.fake_jev import FakeJev

from jevonly import envs
from jevonly.core import keyboard as keyboard_module
from jevonly.core import loop as loop_module
from jevonly.core.loop import run_task


class OneButtonEnv:
    """A page with one button that commits something and one link that does not."""

    def __init__(self, task):
        self.task = task
        self.performed: list[str] = []
        self.ordered = False

    def _cands(self):
        return [
            {
                "id": "order",
                "idx": 0,
                "desc": 'button "Place order"',
                "kind": "click",
                "target_key": "button:Place order",
                "side_effect": "unknown",
            },
            {
                "id": "help",
                "idx": 1,
                "desc": 'link "Help"',
                "kind": "click",
                "target_key": "link:Help",
                "side_effect": "navigation",
            },
        ]

    def candidates(self):
        return self._cands()

    def observe(self):
        return {
            "url": "https://shop.example/cart",
            "title": "Cart",
            "elements": [{"role": "button", "name": "Place order"}, {"role": "link", "name": "Help"}],
            "visible_text": "Cart: 1 item. " + ("Order placed." if self.ordered else "Place order"),
            "headings": ["Cart"],
        }

    def act(self, cand, value=None, kind=None):
        self.performed.append(cand["id"])
        if cand["id"] == "order":
            self.ordered = True

    def undo(self):
        self.ordered = False

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
def scripted_jev(monkeypatch):
    jev = FakeJev()
    # judge+next: not done, on path, pick the order button; risk: irreversible; verify: worked
    jev.add_rule(question="done", noul=0.1)
    jev.add_rule(question="offpath", noul=0.05)
    jev.add_rule(question="target", contains="Place order")
    jev.add_rule(tag="risk", noul=0.9)
    jev.add_rule(question="verify", noul=0.95)
    jev.add_rule(question="progress", noul=0.9)
    jev.add_rule(noul=0.0)
    monkeypatch.setattr(loop_module, "jev", jev)
    monkeypatch.setattr(keyboard_module, "jev", jev)
    return jev


def _task(policy, approve=None, max_steps=1):
    task = {
        "id": "gate",
        "env": "onebutton",
        "goal": "Place the order",
        "start": "https://shop.example/cart",
        "facts": {},
        "terminal": {},
        "faults": {},
        "max_steps": max_steps,
        "irreversible": policy,
    }
    if approve is not None:
        task["approve"] = approve
    return task


@pytest.fixture
def one_button_env():
    created = []

    def factory(task):
        env = OneButtonEnv(task)
        created.append(env)
        return env

    envs.register("onebutton", factory)
    yield created
    envs._REGISTRY.pop("onebutton", None)


def _approvals(sink):
    return [e for e in sink if e["kind"] == "approval"]


def test_refuse_never_performs_the_action(scripted_jev, one_button_env, events_sink):
    run_task(_task("refuse"), variant="noaccept", on_event=events_sink)
    env = one_button_env[0]
    assert "order" not in env.performed
    approvals = _approvals(events_sink)
    assert approvals and approvals[-1]["status"] == "denied" and approvals[-1]["decided_by"] == "policy"


def test_allow_performs_it_without_asking(scripted_jev, one_button_env, events_sink):
    run_task(_task("allow"), variant="noaccept", on_event=events_sink)
    env = one_button_env[0]
    assert env.performed == ["order"]
    assert [e["status"] for e in _approvals(events_sink)] == ["allowed"]


def test_ask_follows_the_approver(scripted_jev, one_button_env, events_sink):
    seen = []

    def approver(info):
        seen.append(info)
        return True

    run_task(_task("ask", approve=approver), variant="noaccept", on_event=events_sink)
    env = one_button_env[0]
    assert env.performed == ["order"]
    assert seen and seen[0]["action"].startswith('button "Place order"') and seen[0]["risk"] == 0.9
    assert [e["status"] for e in _approvals(events_sink)] == ["pending", "allowed"]


def test_ask_without_an_approver_is_a_refusal(scripted_jev, one_button_env, events_sink):
    run_task(_task("ask"), variant="noaccept", on_event=events_sink)
    assert "order" not in one_button_env[0].performed


def test_a_crashing_approver_is_a_refusal(scripted_jev, one_button_env, events_sink):
    def approver(info):
        raise RuntimeError("operator channel down")

    run_task(_task("ask", approve=approver), variant="noaccept", on_event=events_sink)
    assert "order" not in one_button_env[0].performed
