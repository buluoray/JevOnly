"""QA regression mode: a suite of closed tasks against a site you own, each run several times, one pass/fail
row per case.

A case is one JSON object per line of the suite file:

    {"id": "cart-badge", "start": "https://staging.shop.example/", "facts": {"sku": "A-1"},
     "goal": "Add the product with the given sku to the cart and stop when the cart badge shows 1",
     "expect": {"text": "Cart (1)", "answer": ["1"]}}

`expect.text` / `expect.url_contains` become the run's code-owned terminal check (the same check `jevonly
run` uses when a task declares one): the run is a pass only when the page really shows it, whatever Jev
says. `expect.answer` lists substrings that must appear in the reported answer or the copied values.
A case with no `expect` passes on the run's own success flag.

Every run writes its full event log (one JSON object per line) next to the report, so a failed row can be
replayed step by step; the report itself is JSON plus a markdown table. Repeats exist because a QA suite
that passes once proves little: the flake rate is the number that matters.
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from .core.loop import run_task

# TypeSafe's published price for Jev input tokens (docs.typesafe.ai, 2026-09); answers are free. An estimate
# for the table, not a bill: the API's own usage report is what to trust.
USD_PER_M_INPUT_TOKENS = 0.042

DEFAULTS = {
    "max_steps": 28,
    "budget_s": 1800,
    "max_backtracks": 20,
    "max_cands": 200,
    "text_budget": 6000,
    "ctx_budget": 400,
}


def build_task(
    goal,
    start,
    *,
    facts=None,
    terminal=None,
    max_steps=None,
    thresholds=None,
    irreversible="refuse",
    prefilter=True,
    task_id="cli",
    app=None,
    launch=False,
):
    """The task dict the loop runs, as `jevonly run` and `jevonly qa` both build it."""
    return {
        "id": task_id,
        "env": "computer" if app else "browser",
        "start": start or f"app://{app}",
        "app": app,
        "launch": bool(launch),
        "goal": goal,
        "facts": dict(facts or {}),
        "terminal": dict(terminal or {}),
        "faults": {},
        "max_steps": int(max_steps or DEFAULTS["max_steps"]),
        "budget_s": DEFAULTS["budget_s"],
        "max_backtracks": DEFAULTS["max_backtracks"],
        "max_cands": DEFAULTS["max_cands"],
        "text_budget": DEFAULTS["text_budget"],
        "ctx_budget": DEFAULTS["ctx_budget"],
        "thresholds": dict(thresholds or {}),
        "irreversible": irreversible,
        "prefilter": bool(prefilter),
    }


TERMINAL_KEYS = ("text", "url_contains")


def load_suite(path):
    """Cases from a JSONL file. Missing required fields and unknown `expect` keys are errors at load time,
    not surprises after a two-minute run."""
    cases = []
    for lineno, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        case = json.loads(line)
        for key in ("id", "goal", "start"):
            if not case.get(key):
                raise ValueError(f"{path}:{lineno}: case is missing {key!r}")
        expect = case.get("expect") or {}
        unknown = set(expect) - set(TERMINAL_KEYS) - {"answer"}
        if unknown:
            raise ValueError(f"{path}:{lineno}: unknown expect keys {sorted(unknown)}")
        if "answer" in expect and not isinstance(expect["answer"], list):
            raise ValueError(f"{path}:{lineno}: expect.answer must be a list of substrings")
        cases.append(case)
    ids = [c["id"] for c in cases]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"{path}: duplicate case ids {dupes}")
    return cases


def judge(case, log):
    """(passed, reasons). The run's own success flag first; then every `expect.answer` substring against the
    reported answer and the copied values. A run that stopped short of its terminal check already has
    success=False, so `expect.text` needs no second reading here."""
    reasons = []
    if not log.get("success"):
        reasons.append(f"run did not succeed (stopped={log.get('stopped')})")
    wanted = (case.get("expect") or {}).get("answer") or []
    answer = str((log.get("answer") or {}).get("text") or "")
    copied = " ".join(str(v.get("text", "")) for v in (log.get("copied_values") or {}).values())
    haystack = f"{answer} {copied}"
    for needle in wanted:
        if str(needle) not in haystack:
            reasons.append(f"answer lacks {needle!r} (got {answer!r})")
    return (not reasons), reasons


def run_case(case, rep, out_dir, *, variant="std", max_steps=None, prefilter=True):
    """One run of one case: the event log goes to `<out_dir>/<id>-r<rep>.jsonl`, the loop's log comes back
    with `copied_values` (the end event's copied map) attached for `judge`."""
    expect = case.get("expect") or {}
    terminal = {k: expect[k] for k in TERMINAL_KEYS if k in expect}
    task = build_task(
        case["goal"],
        case["start"],
        facts=case.get("facts"),
        terminal=terminal,
        max_steps=case.get("max_steps") or max_steps,
        thresholds=case.get("thresholds"),
        prefilter=prefilter,
        task_id=case["id"],
    )
    path = Path(out_dir) / f"{case['id']}-r{rep}.jsonl"
    end = {}
    with path.open("w", encoding="utf-8") as handle:

        def on_event(kind, payload):
            event = {"kind": kind, "t": time.time(), **payload}
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            if kind == "end":
                end.update(payload)

        log = run_task(task, variant=case.get("variant") or variant, on_event=on_event)
    log["copied_values"] = {k: {"text": v} for k, v in (end.get("copied") or {}).items()}
    log["in_tok"] = int(end.get("in_tok") or 0)
    log["events_path"] = str(path)
    return log


def run_suite(cases, *, repeat=1, out_dir, variant="std", max_steps=None, prefilter=True, on_line=None):
    """Every case `repeat` times, in order, one row per case. Returns the report dict (also written to
    `<out_dir>/qa-report.json`)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in cases:
        runs = []
        for rep in range(1, repeat + 1):
            t0 = time.monotonic()
            try:
                log = run_case(case, rep, out, variant=variant, max_steps=max_steps, prefilter=prefilter)
                passed, reasons = judge(case, log)
                run = {
                    "rep": rep,
                    "passed": passed,
                    "reasons": reasons,
                    "steps": len(log.get("steps") or []),
                    "seconds": log.get("seconds", round(time.monotonic() - t0, 1)),
                    "jev_calls": log.get("jev_calls", 0),
                    "in_tok": log.get("in_tok", 0),
                    "stopped": log.get("stopped"),
                    "answer": (log.get("answer") or {}).get("text"),
                    "events": log.get("events_path"),
                }
            except Exception as exc:  # one crashing case must not hide the rest of the suite
                run = {
                    "rep": rep,
                    "passed": False,
                    "reasons": [f"crashed: {exc!r}"[:200]],
                    "steps": 0,
                    "seconds": round(time.monotonic() - t0, 1),
                    "jev_calls": 0,
                    "in_tok": 0,
                    "stopped": "crashed",
                    "answer": None,
                    "events": None,
                }
            runs.append(run)
            if on_line:
                mark = "PASS" if run["passed"] else "FAIL"
                on_line(
                    f"{case['id']} r{rep}: {mark} steps={run['steps']} {run['seconds']}s"
                    + ("" if run["passed"] else " -- " + "; ".join(run["reasons"]))
                )
        rows.append(summarize(case, runs))
    report = {
        "suite": [c["id"] for c in cases],
        "repeat": repeat,
        "rows": rows,
        "passed": all(r["pass_rate"] == 1.0 for r in rows),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (out / "qa-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "qa-report.md").write_text(render_table(report), encoding="utf-8")
    return report


def summarize(case, runs):
    passes = sum(1 for r in runs if r["passed"])
    med = lambda key: statistics.median([r[key] for r in runs]) if runs else 0  # noqa: E731
    reasons = []
    for r in runs:
        for reason in r["reasons"]:
            if reason not in reasons:
                reasons.append(reason)
    tokens = sum(r["in_tok"] for r in runs)
    return {
        "id": case["id"],
        "runs": len(runs),
        "passes": passes,
        "pass_rate": (passes / len(runs)) if runs else 0.0,
        "steps_median": med("steps"),
        "seconds_median": med("seconds"),
        "jev_calls_median": med("jev_calls"),
        "in_tok": tokens,
        "usd_estimate": round(tokens / 1e6 * USD_PER_M_INPUT_TOKENS, 6),
        "last_answer": runs[-1]["answer"] if runs else None,
        "reasons": reasons,
        "run_logs": [r["events"] for r in runs],
    }


def render_table(report):
    head = "| case | pass | steps | seconds | jev calls | tokens | est. USD | last answer | why failed |"
    rule = "|---|---|---|---|---|---|---|---|---|"
    lines = [head, rule]
    for r in report["rows"]:
        lines.append(
            f"| {r['id']} | {r['passes']}/{r['runs']} | {r['steps_median']:g} | {r['seconds_median']:g} | "
            f"{r['jev_calls_median']:g} | {r['in_tok']} | {r['usd_estimate']:.4f} | "
            f"{str(r['last_answer'] or '-')[:40]} | {'; '.join(r['reasons'])[:120] or '-'} |"
        )
    verdict = "all cases passed every run" if report["passed"] else "some runs failed"
    lines.append("")
    lines.append(f"{len(report['rows'])} cases x {report['repeat']} runs: {verdict}.")
    return "\n".join(lines)
