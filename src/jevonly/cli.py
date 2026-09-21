"""Command-line interface for running JevOnly and its local viewer."""

import argparse
import json
import sys
import time
from pathlib import Path

from .core.loop import run_task


def _short(value, limit):
    text = str(value if value is not None else "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _score(value):
    return f"{float(value):.2f}"


def compact_log_line(kind, event):
    """Render one event in the compact form used by the viewer's copy log."""
    step = f"step {int(event.get('step', 0)) + 1}"
    if kind == "start":
        return f"START variant={event['variant']} code_check={event['has_code_check']} max_steps={event['max_steps']}"
    if kind == "observe":
        return f"{step} OBSERVE elements={event.get('n_elements', 0)} {_short(event.get('url'), 100)}"
    if kind == "judge":
        done = _score(event["done"]) if event.get("done_asked") else "n/a"
        return f"{step} JUDGE done={done} offpath={_score(event['offpath'])}"
    if kind == "plan":
        ranked = " | ".join(f"{_score(item['p'])} {_short(item['desc'], 70)}" for item in event.get("ranked", [])[:4])
        return f"{step} NEXT {ranked}"
    if kind == "act":
        value = f" value={json.dumps(event['value'])}" if event.get("value") is not None else ""
        return f"{step} ACT {event.get('action_kind')} {_short(event.get('desc'), 110)}{value}"
    if kind == "verify":
        progress = f" progress={_score(event['progress'])}" if event.get("progress") is not None else ""
        return f"{step} VERIFY {_score(event['verify'])}{progress} -> {event.get('outcome')}"
    if kind == "undo":
        return f"{step} UNDO {event.get('reason')}"
    if kind == "key":
        return (
            f"{step} KEY {event.get('choice')} p={_score(event.get('p', 0))} field={json.dumps(event.get('typed', ''))}"
        )
    if kind == "note":
        return f"{step} NOTE {event.get('text')}"
    if kind == "copy":
        return f"{step} COPY {json.dumps(event.get('text'))} -> {event.get('key')} ({event.get('rounds')} rounds)"
    if kind == "copy_trace":
        attempts = event.get("attempts", [])
        chosen = next((item.get("text") for item in reversed(attempts) if item.get("accepted")), None)
        return f"{step} COPY_SELECT attempts={len(attempts)} chosen={json.dumps(chosen)}"
    if kind == "answer":
        return f"{step} ANSWER {json.dumps(event.get('text'))} ({event.get('rounds')} rounds)"
    if kind == "end":
        return (
            f"END success={event.get('success')} stopped={event.get('stopped') or 'none'} "
            f"steps={event.get('steps')} jev_calls={event.get('jev_calls')} seconds={event.get('seconds')}"
        )
    return None


def _facts(raw):
    if raw is None:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("--facts must be a JSON object")
    return {str(key): str(item) for key, item in value.items()}


THRESHOLD_NAMES = ("verify", "offpath", "done", "risk")


def _thresholds(pairs):
    """`--threshold verify=0.6 --threshold risk=0.3` -> {"verify": 0.6, "risk": 0.3}."""
    out = {}
    for pair in pairs or ():
        name, _, value = pair.partition("=")
        if name not in THRESHOLD_NAMES:
            raise SystemExit(f"--threshold: unknown name {name!r}; one of {', '.join(THRESHOLD_NAMES)}")
        try:
            number = float(value)
        except ValueError:
            raise SystemExit(f"--threshold {name}: {value!r} is not a number") from None
        if not 0.0 <= number <= 1.0:
            raise SystemExit(f"--threshold {name}: {number} is outside 0..1")
        out[name] = number
    return out


def _ask_on_terminal(info):
    """The `ask` policy on the command line: a yes/no question on the terminal. Without a terminal to ask
    (a pipe, a cron), the answer is no -- an irreversible action never runs on a default."""
    if not sys.stdin.isatty():
        print(
            f"irreversible action needs approval but stdin is not a terminal -> refused: {info['action']}", flush=True
        )
        return False
    answer = input(f"\nIrreversible action (risk {info['risk']:.2f}): {info['action']}\nPerform it? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


def _run(args):
    facts = _facts(args.facts)
    task = {
        "id": "cli",
        "env": "computer" if args.app else "browser",
        "start": args.start or f"app://{args.app}",
        "app": args.app,
        "launch": bool(args.launch),
        "goal": args.goal,
        "facts": facts,
        "terminal": {},
        "faults": {},
        "max_steps": args.max_steps,
        "budget_s": 1800,
        "max_backtracks": 20,
        "max_cands": 200,
        "text_budget": 6000,
        "ctx_budget": 400,
        "thresholds": _thresholds(args.threshold),
        "irreversible": args.irreversible,
        "prefilter": not args.no_prefilter,
    }
    if args.irreversible == "ask":
        task["approve"] = _ask_on_terminal
    output = Path(args.out) if args.out else None
    handle = output.open("w", encoding="utf-8") if output else None
    started = time.time()

    def on_event(kind, payload):
        event = {"kind": kind, "t": time.time(), **payload}
        if handle is not None:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            handle.flush()
        line = compact_log_line(kind, event)
        if line:
            print(f"+{event['t'] - started:6.1f}s {line}", flush=True)

    try:
        result = run_task(task, variant=args.variant, on_event=on_event)
    finally:
        if handle is not None:
            handle.close()
    return 0 if result.get("success") or result.get("stopped") == "stopped_on_done_signal" else 1


def _serve(args):
    from .viewer.server import main as serve_main

    return serve_main(["--port", str(args.port)])


def _parser():
    parser = argparse.ArgumentParser(prog="jevonly", description="Drive a browser using closed-choice judgments only.")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run a browser task")
    run.add_argument("--goal", required=True, help="natural-language goal")
    target = run.add_mutually_exclusive_group(required=True)
    target.add_argument("--start", help="public starting URL (the browser environment)")
    target.add_argument(
        "--app",
        help="a desktop application to drive instead of a web page, by the name the OS shows (macOS/Windows, "
        "needs Kiro Crew with Computer Use switched on)",
    )
    run.add_argument("--launch", action="store_true", help="with --app: open the application if it is not running")
    run.add_argument("--facts", help="JSON object containing known values")
    run.add_argument("--variant", choices=("std", "noaccept_kb"), default="std")
    run.add_argument("--max-steps", type=int, default=28)
    run.add_argument("--out", help="write every event as JSON Lines")
    run.add_argument(
        "--irreversible",
        choices=("refuse", "ask", "allow"),
        default="refuse",
        help="what to do with an action Jev judges irreversible: skip it (default), ask on the terminal, or perform it",
    )
    run.add_argument(
        "--no-prefilter",
        action="store_true",
        help="skip the one-call classifier.dev pre-filter over the page text before a copy (collapse the full page instead)",
    )
    run.add_argument(
        "--threshold",
        action="append",
        metavar="NAME=VALUE",
        help="move a decision line, e.g. --threshold verify=0.6; names: verify, offpath, done, risk (repeatable)",
    )
    run.set_defaults(func=_run)

    serve = commands.add_parser("serve", help="start the loopback viewer")
    serve.add_argument("--port", type=int, default=7791)
    serve.set_defaults(func=_serve)
    return parser


def main(argv=None):
    """Parse command-line arguments and execute the selected command."""
    parser = _parser()
    args = parser.parse_args(argv)
    if getattr(args, "max_steps", 1) < 1:
        parser.error("--max-steps must be at least 1")
    try:
        return_code = args.func(args)
    except (json.JSONDecodeError, argparse.ArgumentTypeError) as exc:
        parser.error(str(exc))
    raise SystemExit(return_code or 0)


if __name__ == "__main__":
    main()
