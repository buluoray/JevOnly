"""The bind question rides on the plan request: for every value-taking candidate on the ballot a speculative
"if this is the pick, which fact / option" head is asked in the SAME request as done/offpath/next, and the
chosen candidate's answer is used without a second round trip."""

from __future__ import annotations

from fake_jev import FakeJev  # noqa: E402
from jevonly import envs
from jevonly.core import keyboard as keyboard_module
from jevonly.core import loop as loop_module
from jevonly.core.loop import run_task
from jevonly.core.questions import FANOUT_MAX_BIND, bind_heads

FACTS = {"origin": "Boston", "destination": "Paris CDG"}


def _cands(n_fields=2, with_select=False):
    out = []
    for i in range(n_fields):
        out.append(
            {
                "id": f"f{i}",
                "idx": i,
                "desc": f'textbox "Field {i}"',
                "kind": "fill",
                "options": None,
                "target_key": f"textbox:Field {i}",
                "needs_commit": True,
                "side_effect": "reversible",
                "fam": None,
                "value": "",
            }
        )
    if with_select:
        out.append(
            {
                "id": "s0",
                "idx": n_fields,
                "desc": 'combobox "Cabin" | options: Economy, Business',
                "kind": "select",
                "options": ["Economy", "Business"],
                "target_key": "combobox:Cabin",
                "side_effect": "reversible",
                "fam": None,
                "value": "",
            }
        )
    out.append(
        {
            "id": "go",
            "idx": 99,
            "desc": 'button "Search"',
            "kind": "click",
            "target_key": "button:Search",
            "side_effect": "reversible",
        }
    )
    return out


def test_bind_heads_one_per_value_taker_with_speculative_wording():
    heads = bind_heads(_cands(2, with_select=True), FACTS)
    assert set(heads) == {"bind_f0", "bind_f1", "bind_s0"}
    assert set(heads["bind_f0"]["criteria"]) == {"origin", "destination", "none"}
    assert set(heads["bind_s0"]["criteria"]) == {"Economy", "Business"}
    assert heads["bind_f1"]["instructions"].startswith('IF the agent\'s next action turns out to be: textbox "Field 1"')
    assert "Which fact should be used as the value?" in heads["bind_f1"]["instructions"]


def test_bind_heads_bounded_and_absent_without_offer():
    assert bind_heads(_cands(FANOUT_MAX_BIND + 1), FACTS) == {}
    assert bind_heads(_cands(2), {}) == {}  # nothing to offer a field -> no head for it
    assert set(bind_heads(_cands(1, with_select=True), {})) == {"bind_s0"}  # a select offers its own options
    assert bind_heads([_cands(0)[-1]], FACTS) == {}  # a button takes no value


class OneFieldEnv:
    def __init__(self, task):
        self.task = task
        self.value = ""
        self.act_calls = []

    def candidates(self):
        cands = _cands(1)
        cands[0]["value"] = self.value
        return cands

    def observe(self):
        return {
            "url": "https://travel.example/",
            "title": "Search",
            "elements": [{"role": "textbox", "name": "Field 0", "value": self.value}],
            "visible_text": f"Where from? {self.value or '(empty)'}",
            "headings": ["Search"],
        }

    def act(self, cand, value=None, kind=None):
        self.act_calls.append((cand["id"], value, kind))
        if cand["id"] == "f0":
            self.value = str(value)

    def undo(self):
        pass

    def fingerprint(self, obs):
        return obs["visible_text"]

    def terminal(self, obs):
        return bool(self.value)

    def acceptance(self, obs):
        return []

    def close(self):
        pass


def _run(monkeypatch, fake):
    monkeypatch.setattr(loop_module, "jev", fake)
    monkeypatch.setattr(keyboard_module, "jev", fake, raising=False)
    monkeypatch.setitem(envs._REGISTRY, "onefield", OneFieldEnv)
    task = {
        "id": "fanout",
        "env": "onefield",
        "goal": "Type the origin into the box and stop",
        "start": "https://travel.example/",
        "facts": dict(FACTS),
        "terminal": {},
        "faults": {},
        "max_steps": 3,
        "irreversible": "refuse",
    }
    return run_task(task, on_event=lambda *_: None)


def _responder(inline_choice):
    def answer(state, questions, tag):
        out = {}
        filled = any(e.get("value") for e in state.get("state", {}).get("elements", []))
        for name, q in questions.items():
            if q["type"] == "noul":
                if name == "offpath" or tag == "risk" or name.startswith(("value_", "needs_")):
                    out[name] = {"noul": 0.05}
                elif name == "done":
                    out[name] = {"noul": 0.95 if filled else 0.05}
                else:
                    out[name] = {"noul": 0.95}
                continue
            crit = q["criteria"]
            if name == "target":
                choice = "none" if filled else next(o for o, d in crit.items() if 'textbox "Field 0"' in d)
            elif name == "bind_f0":
                choice = inline_choice
            elif name == "fact":
                choice = "origin"  # what a follow-up bind request would answer
            elif name == "commit":
                choice = "leave_it"
            elif name == "result":
                choice = "succeeded"
            else:
                choice = next(iter(crit))
            rest = 0.05 / max(1, len(crit) - 1)
            out[name] = {"choice": choice, "probabilities": {o: 0.95 if o == choice else rest for o in crit}}
        return out

    return answer


def test_chosen_field_uses_the_inline_bind_answer_no_second_request(monkeypatch):
    fake = FakeJev(responder=_responder("origin"))
    log = _run(monkeypatch, fake)
    assert log["success"], log
    tags = [c["tag"] for c in fake.calls]
    assert "bind" not in tags, tags
    plan_calls = [c for c in fake.calls if c["tag"] == "judge+next"]
    assert plan_calls and "bind_f0" in plan_calls[0]["question_names"]
    assert log["steps"][0].get("bound_inline") is True


def test_inline_answer_is_load_bearing_wrong_fact_lands_in_the_field(monkeypatch):
    """Mutation guard: if the fanned-out answer were ignored (a second `bind` request asked instead) this
    scenario would type Boston (the `fact` head says origin); with the head honoured it types Paris CDG."""
    fake = FakeJev(responder=_responder("destination"))
    log = _run(monkeypatch, fake)
    typed = [e for s in log["steps"] for e in s["events"] if isinstance(e, dict) and e.get("value")]
    assert typed and typed[0]["value"] == "Paris CDG", typed
