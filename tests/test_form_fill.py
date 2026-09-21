"""A form with several empty fields and matching facts is filled in ONE pass: one binding request, one
action, one verification -- and one undo frame."""

from __future__ import annotations

import pytest

from fake_jev import FakeJev  # noqa: E402  (tests/ is on sys.path via conftest)
from jevonly import envs
from jevonly.core import keyboard as keyboard_module
from jevonly.core import loop as loop_module
from jevonly.core.loop import form_fill_candidate, run_task
from jevonly.core.questions import q_form_bind

FIELDS = [
    ("textbox", "First name", None),
    ("textbox", "Email", None),
    ("textbox", "Promo code", None),
    ("combobox", "Country", ["Canada", "United States"]),
]


class FormEnv:
    def __init__(self, task):
        self.task = task
        self.values = {name: "" for _, name, _ in FIELDS}
        self.act_many_calls = []
        self.act_calls = []
        self.undos = 0

    def candidates(self):
        out = []
        for i, (role, name, options) in enumerate(FIELDS):
            out.append(
                {
                    "id": f"c{i}",
                    "idx": i,
                    "desc": f'{role} "{name}"' + (f" | options: {', '.join(options)}" if options else ""),
                    "kind": "select" if options else "fill",
                    "options": options,
                    "target_key": f"{role}:{name}",
                    "needs_commit": not options,
                    "side_effect": "reversible",
                    "fam": None,
                    "value": self.values[name],
                }
            )
        out.append(
            {
                "id": "submit",
                "idx": len(FIELDS),
                "desc": 'button "Continue"',
                "kind": "click",
                "target_key": "button:Continue",
                "side_effect": "reversible",
            }
        )
        return out

    def observe(self):
        return {
            "url": "https://shop.example/checkout",
            "title": "Checkout",
            "elements": [{"role": r, "name": n, "value": self.values[n]} for r, n, _ in FIELDS],
            "visible_text": "Checkout. " + " ".join(f"{n}: {self.values[n] or '(empty)'}" for _, n, _ in FIELDS),
            "headings": ["Checkout"],
        }

    def act(self, cand, value=None, kind=None):
        self.act_calls.append((cand["id"], value, kind))
        if cand["id"].startswith("c"):
            self.values[FIELDS[cand["idx"]][1]] = str(value)

    def act_many(self, fills):
        self.act_many_calls.append([(c["id"], v, k) for c, v, k in fills])
        for c, v, _ in fills:
            self.values[FIELDS[c["idx"]][1]] = str(v)

    def undo(self):
        self.undos += 1

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
def form_env():
    envs.register("form", FormEnv)
    yield
    envs._REGISTRY.pop("form", None)


def _task(**over):
    task = {
        "id": "checkout",
        "env": "form",
        "goal": "Fill in the checkout form with my details and stop before paying",
        "start": "https://shop.example/checkout",
        "facts": {"first_name": "Ray", "email": "ray@example.com", "country": "United States"},
        "terminal": {},
        "faults": {},
        "max_steps": 3,
        "irreversible": "refuse",
    }
    task.update(over)
    return task


def _jev_for_a_form_pass():
    jev = FakeJev()
    jev.add_rule(question="done", noul=0.1)
    jev.add_rule(question="offpath", noul=0.05)
    jev.add_rule(question="verify", noul=0.95)
    jev.add_rule(question="progress", noul=0.8)
    jev.add_rule(question="target", contains="whole form")
    jev.add_rule(question="field_0", contains="first_name")
    jev.add_rule(question="field_1", contains="email")
    jev.add_rule(question="field_2", choice="none")  # no fact is a promo code
    jev.add_rule(question="field_3", choice="United States")
    jev.add_rule(noul=0.0)
    return jev


def test_form_fill_is_on_the_ballot_only_with_two_empty_fields_and_some_fact():
    cands = FormEnv(_task()).candidates()
    assert form_fill_candidate(cands, {"a": "1"})["id"] == "fill_form"
    assert form_fill_candidate(cands, {}) is None, "nothing to put in the boxes -> not offered"
    one_left = [dict(c, value="x") if c["id"] in ("c1", "c2", "c3") else c for c in cands]
    assert form_fill_candidate(one_left, {"a": "1"}) is None, "a single empty field is an ordinary fill"
    desc = form_fill_candidate(cands, {"a": "1"})["desc"]
    assert "4 empty fields" in desc and "First name" in desc and "nothing is submitted" in desc


def test_one_request_binds_every_field_and_a_select_is_offered_its_options():
    fields = form_fields = loop_module.form_fields(FormEnv(_task()).candidates())
    q = q_form_bind(fields, {"first_name": "Ray", "email": "ray@example.com"}, {"textbox:Email": {"email"}})
    assert list(q) == ["field_0", "field_1", "field_2", "field_3"]
    assert set(q["field_0"]["criteria"]) == {"first_name", "email", "none"}
    assert set(q["field_1"]["criteria"]) == {"first_name", "none"}, "a fact already used for this target is not offered"
    assert set(q["field_3"]["criteria"]) == {"Canada", "United States", "none"}
    assert len(form_fields) == 4


def test_the_whole_form_is_filled_in_one_action_and_verified_once(monkeypatch, form_env, events_sink):
    jev = _jev_for_a_form_pass()
    monkeypatch.setattr(loop_module, "jev", jev)
    monkeypatch.setattr(keyboard_module, "jev", jev)
    holder = {}
    real_init = FormEnv.__init__

    def capture(self, task):
        real_init(self, task)
        holder["env"] = self

    monkeypatch.setattr(FormEnv, "__init__", capture)

    def stop_after_first_step(kind, payload):
        events_sink(kind, payload)
        if kind == "act":
            jev.rules.insert(0, {"question": "done", "noul": 0.95})
            jev.rules.insert(0, {"question": "target", "choice": "none"})

    run_task(_task(), variant="noaccept", on_event=stop_after_first_step)
    env = holder["env"]
    assert env.act_many_calls == [
        [("c0", "Ray", "fill"), ("c1", "ray@example.com", "fill"), ("c3", "United States", "select")]
    ]
    assert env.act_calls == [], "no field was filled one at a time"
    assert env.values == {"First name": "Ray", "Email": "ray@example.com", "Promo code": "", "Country": "United States"}
    assert env.undos == 0
    binds = [c for c in jev.calls if c["tag"] == "bind"]
    assert len(binds) == 1 and binds[0]["question_names"] == ("field_0", "field_1", "field_2", "field_3")
    assert "form_fields" in binds[0]["state_keys"]
    verifies = [c for c in jev.calls if "verify" in c["question_names"]]
    assert len(verifies) == 1, "one verification for the whole pass, not one per field"
    acts = [e for e in events_sink if e["kind"] == "act"]
    assert acts[0]["action_kind"] == "fill_form"
    assert acts[0]["desc"].startswith(
        "filled 3 of 4 form fields in one pass: First name = Ray; Email = ray@example.com"
    )
    end = [e for e in events_sink if e["kind"] == "end"][-1]
    assert end["success"] is True


def test_a_pass_that_binds_nothing_is_skipped_without_touching_the_page(monkeypatch, form_env, events_sink):
    jev = _jev_for_a_form_pass()
    for i in range(4):
        jev.rules = [r for r in jev.rules if r.get("question") != f"field_{i}"]
        jev.rules.insert(0, {"question": f"field_{i}", "choice": "none"})
    monkeypatch.setattr(loop_module, "jev", jev)
    monkeypatch.setattr(keyboard_module, "jev", jev)
    holder = {}
    real_init = FormEnv.__init__

    def capture(self, task):
        real_init(self, task)
        holder["env"] = self

    monkeypatch.setattr(FormEnv, "__init__", capture)

    def steer(kind, payload):
        events_sink(kind, payload)
        if kind == "plan":
            jev.rules.insert(0, {"question": "done", "noul": 0.95})
            jev.rules.insert(0, {"question": "target", "choice": "none"})

    log = run_task(_task(max_steps=2), variant="noaccept", on_event=steer)
    env = holder["env"]
    assert env.act_many_calls == [] and env.act_calls == [] and env.values["First name"] == ""
    outcomes = [e.get("outcome", "") for s in log["steps"] for e in s["events"] if isinstance(e, dict)]
    assert any("no fact fits any of the 4 fields" in o for o in outcomes)
    assert not [c for c in jev.calls if "verify" in c["question_names"]], "nothing was done, nothing to verify"
