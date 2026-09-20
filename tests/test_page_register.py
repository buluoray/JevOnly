"""The register is keyed by (clause, page): one clause can collect one value per page, even the same text."""

from __future__ import annotations

import pytest

from fake_jev import FakeJev  # noqa: E402  (tests/ is on sys.path via conftest)
from jevonly import envs
from jevonly.core import keyboard as keyboard_module
from jevonly.core import loop as loop_module
from jevonly.core.loop import page_key, run_task

PAGES = {
    "A": ("https://wiki.example/Seattle?oldid=1", "Seattle", "Columbia Center is the tallest building at 286 m."),
    "B": ("https://wiki.example/Portland?oldid=2", "Portland", "Wells Fargo Center is the tallest building at 286 m."),
}


def test_page_key_drops_the_query_and_keeps_a_hash_route():
    assert page_key("https://a.example/s?k=x&qid=1#top") == "https://a.example/s"
    assert page_key("https://a.example/s?k=x") == page_key("https://a.example/s?k=y&qid=2")
    assert page_key("https://app.example/#/orders/12?tab=all") == "https://app.example/#/orders/12"
    assert page_key("") == ""


class TwoPageEnv:
    def __init__(self, task):
        self.task = task
        self.page = "A"

    def candidates(self):
        return [
            {
                "id": "next",
                "idx": 0,
                "desc": 'link "Portland"',
                "kind": "click",
                "target_key": "link:Portland",
                "side_effect": "navigation",
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
        url, title, line = PAGES[self.page]
        return {
            "url": url,
            "title": title,
            "elements": [{"role": "link", "name": "Portland", "context": line}],
            "visible_text": f"{title}. {line}",
            "headings": [title],
        }

    def act(self, cand, value=None, kind=None):
        if cand["id"] == "next":
            self.page = "B"

    def undo(self):
        self.page = "A"

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
def two_page_env():
    envs.register("twopage", TwoPageEnv)
    yield
    envs._REGISTRY.pop("twopage", None)


def test_the_same_clause_collects_one_value_per_page_even_when_the_text_repeats(monkeypatch, two_page_env, events_sink):
    jev = FakeJev()
    jev.add_rule(question="value_0", noul=0.05)  # "Open Seattle's article" is a thing to do
    jev.add_rule(question="value_1", noul=0.9)  # "note its height in meters"
    jev.add_rule(question="value_2", noul=0.05)  # "then open Portland's article" is a thing to do
    jev.add_rule(question="done", noul=0.2)
    jev.add_rule(question="offpath", noul=0.05)
    jev.add_rule(question="verify", noul=0.95)
    jev.add_rule(question="progress", noul=0.8)
    jev.add_rule(question="target", contains="copy a value")
    jev.add_rule(question="wanted", contains="height")
    jev.add_rule(question="pick", tag="copy", contains="286")
    jev.add_rule(question="ok", noul=0.95)
    jev.add_rule(noul=0.0)
    monkeypatch.setattr(loop_module, "jev", jev)
    monkeypatch.setattr(keyboard_module, "jev", jev)

    seen = {"portland_copy": False}

    def on_event(kind, payload):
        events_sink(kind, payload)
        if kind == "copy" and payload["step"] == 0:
            # the height is in hand on Seattle's page: the planner now moves on to Portland
            jev.rules = [r for r in jev.rules if r.get("question") != "target"]
            jev.rules.insert(0, {"question": "target", "contains": "Portland"})
        if kind == "observe" and payload["title"] == "Portland" and not seen["portland_copy"]:
            jev.rules = [r for r in jev.rules if r.get("question") != "target"]
            jev.rules.insert(0, {"question": "target", "contains": "copy a value"})
        if kind == "copy" and payload["step"] > 0:
            seen["portland_copy"] = True
            jev.rules = [r for r in jev.rules if r.get("question") not in ("wanted", "target")]
            jev.rules.insert(0, {"question": "wanted", "choice": "none"})
            jev.rules.insert(0, {"question": "target", "choice": "none"})

    task = {
        "id": "twoheights",
        "env": "twopage",
        "goal": "Open Seattle's article and note its height in meters, then open Portland's article and note its height in meters",
        "start": PAGES["A"][0],
        "facts": {},
        "terminal": {},
        "faults": {},
        "max_steps": 6,
        "irreversible": "refuse",
    }
    run_task(task, variant="noaccept", on_event=on_event)
    copies = [e for e in events_sink if e["kind"] == "copy"]
    assert [c["text"] for c in copies] == ["286", "286"], (
        "one value per page for the clause, the repeated text notwithstanding"
    )
    assert len({c["key"] for c in copies}) == 2
    notes = [e["text"] for e in events_sink if e["kind"] == "note"]
    assert not any("already copied" in n for n in notes), "a value read on another page is not a duplicate"
    end = [e for e in events_sink if e["kind"] == "end"][-1]
    assert end["success"] is True
