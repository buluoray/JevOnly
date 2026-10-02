import importlib
import os

import pytest

from jevonly.core import jev
from jevonly.core.loop import _same_place


def test_same_url_and_headings_is_the_same_place():
    before = {"url": "http://localhost:1/settings?token=a#x", "headings": ["Settings"]}
    after = {"url": "http://localhost:1/settings", "headings": ["Settings"]}
    assert _same_place(before, after)


def test_a_new_path_or_new_headings_is_somewhere_else():
    base = {"url": "http://localhost:1/settings", "headings": ["Settings"]}
    assert not _same_place(base, {"url": "http://localhost:1/settings/display", "headings": ["Settings"]})
    assert not _same_place(base, {"url": "http://localhost:1/settings", "headings": ["Display"]})


@pytest.mark.parametrize("value", ["example.com:8080", "10.0.0.5:80", "evil.localhost.example:1"])
def test_a_non_loopback_local_endpoint_is_refused(monkeypatch, value):
    monkeypatch.setenv("JEVONLY_LOCAL", value)
    with pytest.raises(RuntimeError, match="loopback"):
        importlib.reload(jev)
    monkeypatch.delenv("JEVONLY_LOCAL")
    importlib.reload(jev)


@pytest.mark.parametrize("value", ["127.0.0.1:8106", "localhost:9", "[::1]:8106"])
def test_a_loopback_local_endpoint_is_accepted(monkeypatch, value):
    monkeypatch.setenv("JEVONLY_LOCAL", value)
    try:
        importlib.reload(jev)
        assert value == jev.LOCAL
    finally:
        monkeypatch.delenv("JEVONLY_LOCAL")
        importlib.reload(jev)
    assert os.environ.get("JEVONLY_LOCAL") is None


def test_a_one_option_choice_is_answered_without_asking():
    q = {"type": "choice", "instructions": "x", "criteria": {"only": "the one"}}
    assert jev._trivial(q)["choice"] == "only"
    assert jev._trivial({"type": "choice", "criteria": {"a": "", "b": ""}}) is None


def _env(candidates):
    from jevonly.envs.browser.env import BrowserEnv

    env = BrowserEnv.__new__(BrowserEnv)  # no browser child: the check only reads the snapshot
    env._snap = {"url": "http://localhost:1/s", "candidates": candidates, "visible_text": "", "headings": []}
    env.task = {"terminal": {"selected": "Dark"}}
    return env


def test_selected_check_reads_the_named_controls_aria_state():
    off = _env(
        [{"role": "button", "name": "Dark", "selected": False}, {"role": "button", "name": "Auto", "selected": True}]
    )
    on = _env([{"role": "button", "name": "Dark", "selected": True}])
    assert not off._is_selected("Dark")
    assert on._is_selected("Dark")
    assert on.acceptance(None) == ['expected the control "Dark" to be selected: selected']
