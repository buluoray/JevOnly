"""The computer environment against a scripted desktop: observation mapping, snapshot-scoped acting,
compensating undo, and the loop's handling of an undo that could not restore the state."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from fake_jev import FakeJev  # noqa: E402  (tests/ is on sys.path via conftest)
from jevonly import envs
from jevonly.core import keyboard as keyboard_module
from jevonly.core import loop as loop_module
from jevonly.core.loop import run_task, undo_failure
from jevonly.core.text import page_units
from jevonly.envs.computer import ComputerEnv, DriverError, Elem, Stale, Tree


def _tree(elements, title="Notes", token=1, selected=""):
    return Tree(
        app_key="41/7",
        app_name="Notes",
        window_title=title,
        elements=list(elements),
        token=token,
        selected_text=selected,
    )


NOTES = [
    Elem(0, "window", title="Notes", depth=0),
    Elem(1, "toolbar", title="Toolbar", depth=1),
    Elem(
        2, "button", title="New Note", actions=("AXPress",), depth=2, path=("Notes", "Toolbar"), frame=(10, 5, 40, 20)
    ),
    Elem(3, "button", title="Delete", actions=("AXPress",), depth=2, path=("Notes", "Toolbar"), frame=(60, 5, 40, 20)),
    Elem(
        4, "button", title="Delete", actions=("AXPress",), depth=2, path=("Notes", "Toolbar"), frame=(700, 500, 40, 20)
    ),
    Elem(5, "group", depth=1),  # nameless layout box: never a candidate
    Elem(6, "textfield", title="Title", value="", editable=True, depth=2, path=("Notes",)),
    Elem(7, "textfield", title="Password", secure=True, value="hunter2", depth=2, path=("Notes",)),
    Elem(8, "button", title="Ghost", actions=("AXPress",), enabled=False, depth=2, path=("Notes",)),
    Elem(9, "row", depth=2, path=("Notes",)),
    Elem(10, "statictext", value="Groceries", depth=3, path=("Notes",)),
    Elem(11, "statictext", value="Sept 20", depth=3, path=("Notes",)),
    Elem(12, "statictext", value="Milk, eggs, bread", depth=2, path=("Notes",)),
    Elem(13, "scrollarea", depth=1),
    Elem(14, "popupbutton", title="Folder", value="All", actions=("AXPress",), depth=2, path=("Notes",)),
]


class FakeDriver:
    """Scripted desktop: one current tree, every mutation drift-checked and recorded."""

    def __init__(self, tree):
        self.tree = tree
        self.calls = []
        self.next_after_press = None  # a tree to switch to after the next press
        self.readback = True  # whether set_value is reflected in the tree's value

    def resolve(self, query):
        return ("app", query)

    def launch(self, query):
        raise DriverError("launch not scripted")

    def snapshot(self, app):
        return self.tree

    def _check(self, tree, index):
        cur = next((e for e in self.tree.elements if e.index == index), None)
        old = next((e for e in tree.elements if e.index == index), None)
        if cur is None or old is None or (cur.role, cur.title) != (old.role, old.title):
            raise Stale(f"element {index} changed since the last walk")
        return cur

    def _bump(self, elements):
        self.tree = replace(self.tree, elements=list(elements), token=self.tree.token + 1)
        return self.tree

    def press(self, app, tree, index):
        e = self._check(tree, index)
        self.calls.append(("press", index, e.title))
        if self.next_after_press is not None:
            self.tree, self.next_after_press = self.next_after_press, None
            return self.tree
        return self._bump(self.tree.elements)

    def set_value(self, app, tree, index, value):
        self._check(tree, index)
        self.calls.append(("set_value", index, value))
        els = [replace(e, value=value) if e.index == index and self.readback else e for e in self.tree.elements]
        return self._bump(els)

    def type_text(self, app, tree, index, text):
        self._check(tree, index)
        self.calls.append(("type_text", index, text))
        els = [replace(e, value=(e.value or "") + text) if e.index == index else e for e in self.tree.elements]
        return self._bump(els)

    def press_key(self, app, tree, index, key):
        self._check(tree, index)
        self.calls.append(("press_key", index, key))
        return self._bump(self.tree.elements)

    def scroll(self, app, tree, index, direction):
        self._check(tree, index)
        self.calls.append(("scroll", index, direction))
        return self._bump(self.tree.elements)

    def end(self):
        self.calls.append(("end",))


def _env(elements=NOTES, **task):
    drv = FakeDriver(_tree(elements))
    env = ComputerEnv({"app": "Notes", "goal": "test", "facts": {}, **task}, driver=drv)
    return env, drv


def test_observe_lists_only_what_a_person_can_act_on():
    env, _ = _env()
    obs = env.observe()
    names = [(e["role"], e["name"]) for e in obs["elements"]]
    assert ("button", "New Note") in names and ("textfield", "Title") in names and ("popupbutton", "Folder") in names
    assert ("textfield", "Password") not in names, "a secure field is never listed"
    assert ("button", "Ghost") not in names, "a disabled control is not offered"
    assert ("group", "") not in names, "a nameless layout box is not a control"
    assert "hunter2" not in obs["visible_text"]
    assert obs["url"] == "app://notes/41/7" and obs["title"] == "Notes"
    ctx = next(e for e in obs["elements"] if e["name"] == "New Note")["context"]
    assert ctx == "Toolbar", "context is the nearest titled ancestor, not the element's own title"


def test_text_units_tie_a_row_together_and_label_field_values():
    env, _ = _env()
    obs = env.observe()
    units = page_units(obs, env._snap)
    assert "Groceries | Sept 20" in units, "a row is one unit joining its descendants"
    assert "Folder: All" in units, "a field with a value reads as label: value"
    assert "Milk, eggs, bread" in units
    assert not any("hunter2" in u for u in units)


def test_candidates_carry_kind_side_effect_and_disambiguation():
    env, _ = _env()
    env.observe()
    cands = {c["desc"]: c for c in env.candidates()}
    by_key = {}
    for c in env.candidates():
        by_key.setdefault(c["target_key"], []).append(c)
    assert by_key["textfield:Title"][0]["kind"] == "fill"
    assert by_key["button:New Note"][0]["kind"] == "click"
    assert by_key["button:New Note"][0]["side_effect"] == "unknown", "an ordinary press is left to the risk question"
    deletes = by_key["button:Delete"]
    assert len(deletes) == 2 and all(c["side_effect"] == "irreversible" for c in deletes)
    assert {c["desc"].split(" | ")[-1] for c in deletes} == {"at the top-left", "at the bottom-right"}
    assert any("opens a menu" in d for d in cands)
    assert {c["scroll_dir"] for c in env.candidates() if c["kind"] == "scroll"} == {"down", "up"}
    assert len(set(c["id"] for c in env.candidates())) == len(env.candidates()), "ids are unique"


def test_act_refuses_a_candidate_from_an_older_walk():
    env, drv = _env()
    env.observe()
    stale = next(c for c in env.candidates() if c["target_key"] == "button:New Note")
    drv._bump(drv.tree.elements)  # the window moved on
    env.observe()
    env.act(stale)
    assert env.last_action_error and env.last_action_error.startswith("stale:")
    assert not any(c[0] == "press" for c in drv.calls), "nothing was pressed on a stale index"


def test_fill_sets_the_value_and_types_when_the_set_does_not_read_back():
    env, drv = _env()
    env.observe()
    title = next(c for c in env.candidates() if c["target_key"] == "textfield:Title")
    env.act(title, "Shopping", "fill")
    assert drv.calls == [("set_value", 6, "Shopping")]
    assert any(e.get("value") == "Shopping" for e in env.observe()["elements"])

    env2, drv2 = _env()
    drv2.readback = False
    env2.observe()
    title = next(c for c in env2.candidates() if c["target_key"] == "textfield:Title")
    env2.act(title, "Shopping", "fill")
    assert [c[0] for c in drv2.calls] == ["set_value", "type_text"]
    assert "typed the value instead" in (env2.last_action_note or "")


def test_undo_restores_a_written_field_and_reports_a_press_it_cannot_take_back():
    env, drv = _env()
    env.observe()
    title = next(c for c in env.candidates() if c["target_key"] == "textfield:Title")
    env.act(title, "Shopping", "fill")
    env.observe()  # the verify observation: the write becomes the frame undo restores
    assert env.undo() == {"restored": True, "error": None}
    assert drv.calls[-1] == ("set_value", 6, "")

    env, drv = _env()
    env.observe()
    new_note = next(c for c in env.candidates() if c["target_key"] == "button:New Note")
    env.act(new_note)
    env.observe()
    r = env.undo()
    assert r["restored"] is False and "New Note" in r["error"]
    assert not any(c[0] == "press_key" for c in drv.calls), "no blind Escape or Cmd+Z"


def test_undo_closes_a_menu_the_press_opened():
    env, drv = _env()
    env.observe()
    folder = next(c for c in env.candidates() if c["target_key"] == "popupbutton:Folder")
    opened = [replace(e, expanded=True) if e.index == 14 else e for e in NOTES]
    opened += [Elem(20, "menu", depth=3), Elem(21, "menuitem", title="Work", actions=("AXPress",), depth=4)]
    drv.next_after_press = _tree(opened, token=2)
    env.act(folder)
    env.observe()
    assert env.undo()["restored"] is True
    assert drv.calls[-1] == ("press_key", 14, "escape")


def test_keyboard_reports_suggestions_that_appeared():
    env, drv = _env()
    env.observe()
    title = next(c for c in env.candidates() if c["target_key"] == "textfield:Title")
    r = env.keyboard(title, focus=True)
    assert r["typed"] == ""
    els = [replace(e, value="Sho") if e.index == 6 else e for e in drv.tree.elements]
    els.append(Elem(30, "menuitem", title="Shopping list", actions=("AXPress",), depth=3))
    drv.tree = _tree(els, token=drv.tree.token + 1)

    def typed(app, tree, index, text):
        drv.calls.append(("type_text", index, text))
        return drv.tree

    drv.type_text = typed
    r = env.keyboard(text="Sho")
    assert r["typed"] == "Sho" and r["options"] == ["Shopping list"]
    r = env.keyboard(key="Escape")
    assert drv.calls[-1] == ("press_key", 6, "escape")


def test_undo_failure_reads_the_environment_result():
    class Browserish:
        def undo(self):
            return None

    class Refusing:
        def undo(self):
            return {"restored": False, "error": "a press cannot be taken back"}

    assert undo_failure(Browserish()) is None
    assert undo_failure(Refusing()) == "a press cannot be taken back"


def test_loop_escalates_when_the_environment_cannot_restore_the_state(monkeypatch, events_sink):
    """Verify rejects the press; the desktop cannot take it back; the loop stops instead of narrating a
    restored state. The browser path (undo returns None) is unchanged."""
    drv = FakeDriver(_tree(NOTES))
    after = [replace(e, title="Untitled Note") if e.index == 0 else e for e in NOTES]
    drv.next_after_press = _tree(after, title="Untitled Note", token=2)
    envs.register("computer-fake", lambda task: ComputerEnv(task, driver=drv))
    jev = FakeJev()
    jev.add_rule(question="target", contains="New Note")
    jev.add_rule(noul=0.0)  # verify says the press did not do what the task needs; risk, offpath, done all low
    monkeypatch.setattr(loop_module, "jev", jev)
    monkeypatch.setattr(keyboard_module, "jev", jev)
    task = {
        "id": "undo-fails",
        "env": "computer-fake",
        "app": "Notes",
        "goal": "Open the note called Groceries",
        "facts": {},
        "terminal": {},
        "faults": {},
        "max_steps": 4,
        "irreversible": "refuse",
    }
    log = run_task(task, variant="noaccept", on_event=events_sink)
    assert log["stopped"] == "escalate_undo_failed" and log["escalated"] is True
    assert [c for c in drv.calls if c[0] == "press"] == [("press", 2, "New Note")]
    assert not any(c[0] == "press_key" for c in drv.calls)
    verify = [e for e in events_sink if e["kind"] == "verify"][-1]
    assert "undo failed" in (verify.get("outcome") or "")


def test_fill_form_is_not_offered_when_the_environment_has_no_atomic_batch():
    env, _ = _env()
    assert env.supports_atomic_batch is False
    # The loop applies the flag at the one place the whole-form pass is put on the ballot.
    src = Path(loop_module.__file__).read_text(encoding="utf-8")
    line = next(line for line in src.splitlines() if "ff = form_fill_candidate(" in line)
    assert 'getattr(env, "supports_atomic_batch", True)' in line


def test_registry_knows_the_computer_environment():
    with pytest.raises(RuntimeError, match="computer"):
        envs.make({"env": "nope"})
