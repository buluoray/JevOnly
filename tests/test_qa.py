"""`jevonly qa`: a suite file becomes one pass/fail row per case, judged on the run's success flag plus the
expected answer substrings, repeated to expose flakes, with every run's event log kept on disk."""

from __future__ import annotations

import json

import pytest

from jevonly import qa


def _write_suite(tmp_path, lines):
    path = tmp_path / "suite.jsonl"
    path.write_text(
        "\n".join(json.dumps(item) if isinstance(item, dict) else item for item in lines) + "\n", encoding="utf-8"
    )
    return path


CASE = {
    "id": "hn-top",
    "start": "https://news.ycombinator.com",
    "goal": "Note the points of the top story and stop",
    "expect": {"answer": ["points"]},
}


def test_load_suite_validates_shape(tmp_path):
    cases = qa.load_suite(_write_suite(tmp_path, ["# a comment", "", CASE]))
    assert [c["id"] for c in cases] == ["hn-top"]
    with pytest.raises(ValueError, match="missing 'start'"):
        qa.load_suite(_write_suite(tmp_path, [{**CASE, "start": ""}]))
    with pytest.raises(ValueError, match="unknown expect keys \\['screenshot'\\]"):
        qa.load_suite(_write_suite(tmp_path, [{**CASE, "expect": {"screenshot": True}}]))
    with pytest.raises(ValueError, match="expect.answer must be a list"):
        qa.load_suite(_write_suite(tmp_path, [{**CASE, "expect": {"answer": "points"}}]))
    with pytest.raises(ValueError, match="duplicate case ids \\['hn-top'\\]"):
        qa.load_suite(_write_suite(tmp_path, [CASE, CASE]))


def test_expect_text_becomes_the_code_owned_terminal_check(monkeypatch, tmp_path):
    seen = {}

    def fake_run_task(task, variant="std", on_event=None):
        seen.update(task)
        on_event("end", {"success": True, "copied": {}, "in_tok": 10})
        return {"success": True, "steps": [], "seconds": 1.0, "jev_calls": 2, "answer": None}

    monkeypatch.setattr(qa, "run_task", fake_run_task)
    case = {**CASE, "expect": {"text": "Cart (1)", "answer": ["1"]}, "facts": {"sku": "A-1"}}
    qa.run_case(case, 1, tmp_path)
    assert seen["terminal"] == {"text": "Cart (1)"}  # `answer` is judged here, not by the loop
    assert seen["facts"] == {"sku": "A-1"}
    assert seen["id"] == "hn-top"


def test_judge_needs_success_and_every_expected_substring():
    ok = {"success": True, "answer": {"text": "212 points"}, "copied_values": {}}
    assert qa.judge(CASE, ok) == (True, [])
    passed, reasons = qa.judge(CASE, {"success": True, "answer": {"text": "212"}, "copied_values": {}})
    assert not passed and reasons == ["answer lacks 'points' (got '212')"]
    passed, reasons = qa.judge(CASE, {"success": False, "stopped": "dead_end", "answer": None})
    assert not passed and reasons[0] == "run did not succeed (stopped=dead_end)"
    # a copied value counts as part of the answer
    assert qa.judge(CASE, {"success": True, "answer": None, "copied_values": {"c": {"text": "101 points"}}})[0]


def _scripted_run_task(outcomes):
    """success flags consumed in order; each run also emits an end event with token usage."""
    queue = list(outcomes)

    def fake_run_task(task, variant="std", on_event=None):
        success = queue.pop(0)
        on_event("step", {"n": 1})
        on_event("end", {"success": success, "copied": {}, "in_tok": 1000})
        return {
            "success": success,
            "stopped": "stopped_on_done_signal" if success else "gave_up_none_streak",
            "steps": [{}, {}, {}],
            "seconds": 4.0,
            "jev_calls": 12,
            "answer": {"text": "212 points"} if success else None,
        }

    return fake_run_task


def test_run_suite_reports_flakes_and_keeps_every_run_log(monkeypatch, tmp_path):
    monkeypatch.setattr(qa, "run_task", _scripted_run_task([True, False, True, True, True, True]))
    flaky = {**CASE}
    steady = {**CASE, "id": "steady", "expect": {}}
    lines = []
    report = qa.run_suite([flaky, steady], repeat=3, out_dir=tmp_path / "out", on_line=lines.append)
    rows = {r["id"]: r for r in report["rows"]}
    assert rows["hn-top"]["passes"] == 2 and rows["hn-top"]["pass_rate"] == pytest.approx(2 / 3)
    assert rows["hn-top"]["reasons"] == [
        "run did not succeed (stopped=gave_up_none_streak)",
        "answer lacks 'points' (got '')",
    ]
    assert rows["steady"]["passes"] == 3
    assert report["passed"] is False
    assert rows["hn-top"]["in_tok"] == 3000 and rows["hn-top"]["usd_estimate"] == pytest.approx(
        3000 / 1e6 * qa.USD_PER_M_INPUT_TOKENS
    )
    # every run's events are on disk, and the report exists in both shapes
    for path in rows["hn-top"]["run_logs"] + rows["steady"]["run_logs"]:
        assert path and (tmp_path / "out" / path.split("/")[-1]).exists()
    assert json.loads((tmp_path / "out" / "qa-report.json").read_text())["repeat"] == 3
    table = (tmp_path / "out" / "qa-report.md").read_text()
    assert "| hn-top | 2/3 |" in table and "| steady | 3/3 |" in table
    assert "some runs failed" in table
    assert lines[1].startswith("hn-top r2: FAIL steps=3 4.0s -- run did not succeed")


def test_a_crashing_case_does_not_hide_the_rest(monkeypatch, tmp_path):
    calls = {"n": 0}

    def boom_then_fine(task, variant="std", on_event=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("browser server died")
        on_event("end", {"success": True, "copied": {}, "in_tok": 0})
        return {"success": True, "steps": [], "seconds": 1.0, "jev_calls": 1, "answer": {"text": "points"}}

    monkeypatch.setattr(qa, "run_task", boom_then_fine)
    report = qa.run_suite([{**CASE, "id": "a"}, {**CASE, "id": "b"}], repeat=1, out_dir=tmp_path)
    rows = {r["id"]: r for r in report["rows"]}
    assert rows["a"]["passes"] == 0 and rows["a"]["reasons"][0].startswith("crashed: RuntimeError")
    assert rows["b"]["passes"] == 1


def test_cli_qa_exit_code_follows_the_report(monkeypatch, tmp_path, capsys):
    from jevonly import cli

    def exit_code(argv):
        with pytest.raises(SystemExit) as info:
            cli.main(argv)
        return info.value.code

    suite = _write_suite(tmp_path, [CASE])
    monkeypatch.setattr(qa, "run_task", _scripted_run_task([False]))
    assert exit_code(["qa", str(suite), "--out", str(tmp_path / "o1")]) == 1
    monkeypatch.setattr(qa, "run_task", _scripted_run_task([True, True]))
    assert exit_code(["qa", str(suite), "--repeat", "2", "--out", str(tmp_path / "o2")]) == 0
    out = capsys.readouterr().out
    assert "| hn-top | 2/2 |" in out and "all cases passed every run" in out
    assert exit_code(["qa", str(tmp_path / "missing.jsonl"), "--out", str(tmp_path / "o3")]) == 2
