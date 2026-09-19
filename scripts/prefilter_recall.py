"""Offline check: would a classifier.dev pre-filter on page elements hurt JevOnly?

Replays the ``judge+next`` calls recorded in viewer-downloaded run logs. Every
candidate element the plan step saw is classified once (per goal) as
``could help with the task`` / ``unrelated to the task``; a candidate survives
when it is labelled helpful OR the classifier is unsure (confidence below
``--keep-below``), and meta actions (scroll / copy / find / none) always survive.

Reported per policy:
  recall   -- how often the action Jev actually chose survives the filter
  kept     -- share of element candidates that survive (1 - cut)
  tokens   -- rough plan-call token saving, assuming tokens scale with candidates

    python scripts/prefilter_recall.py ~/runs/*.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

API = "https://classifier.dev"
LABELS = ["could help with the task", "unrelated to the task"]
META_PREFIXES = ("scroll ", "copy a value", "find a text", "(no listed action")


def is_meta(desc: str) -> bool:
    return desc.startswith(META_PREFIXES)


def classify(goal: str, descs: list[str]) -> list[dict]:
    out: list[dict] = []
    for start in range(0, len(descs), 1000):
        body = json.dumps(
            {
                "labels": LABELS,
                "inputs": descs[start : start + 1000],
                "instructions": (
                    "Each text describes one interactive element on a web page (role, label, hints). "
                    f"'could help with the task' means clicking or typing into it could plausibly be a step "
                    f"towards this goal: {goal} Include search boxes, filters, dates, navigation and anything "
                    "an intermediate step might need. When in doubt, keep it."
                ),
            }
        ).encode()
        req = urllib.request.Request(
            API, data=body, headers={"content-type": "application/json", "user-agent": "jevonly-prefilter/1.0"}
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.load(resp)
        out.extend(payload["results"])
        sys.stderr.write(f"  classified {len(out)}/{len(descs)} ({payload['model']})\n")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--cache", default="", help="json file to cache classifier verdicts between runs")
    parser.add_argument("--keep-below", type=float, default=0.8, help="unsure threshold for the recipe policy")
    args = parser.parse_args(argv)

    # plan events: (goal, candidates[{id, action}], chosen id, jev's top id, in_tok)
    events: list[tuple[str, list[dict], str | None, str, int]] = []
    for path in args.logs:
        acts: dict[int, str] = {}
        plans: list[tuple[int, dict]] = []
        for line in path.open(encoding="utf-8"):
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("kind") == "act" and e.get("attempt", 0) == 0:
                acts.setdefault(e["step"], e["id"])
            elif e.get("kind") == "jev" and e.get("tag") == "judge+next":
                plans.append((e.get("seq", 0), e))
        # a jev event has no step; pair each plan call with the next act by order
        for _, e in plans:
            target = e["answers"].get("target") or {}
            events.append(
                (e["state"]["task_goal"], e["state"]["candidates"], None, target.get("choice", ""), e.get("in_tok", 0))
            )
        # attach executed acts by position (plan i -> the i-th act whose id is among its candidates)
        act_ids = [acts[s] for s in sorted(acts)]
        j = 0
        for i in range(len(events) - len(plans), len(events)):
            goal, cands, _, top, tok = events[i]
            ids = {c["id"] for c in cands}
            while j < len(act_ids) and act_ids[j] not in ids:
                j += 1
            chosen = act_ids[j] if j < len(act_ids) else None
            if chosen:
                j += 1
            events[i] = (goal, cands, chosen, top, tok)
    if not events:
        print("no judge+next events found (only viewer-downloaded logs carry them)", file=sys.stderr)
        return 1

    # classify each (goal, desc) once
    by_goal: dict[str, set[str]] = defaultdict(set)
    for goal, cands, _, _, _ in events:
        for c in cands:
            if not is_meta(c["action"]):
                by_goal[goal].add(c["action"])
    cache_path = Path(args.cache) if args.cache else None
    cache: dict[str, dict] = json.loads(cache_path.read_text()) if cache_path and cache_path.exists() else {}
    verdict: dict[tuple[str, str], dict] = {}
    for goal, descs in by_goal.items():
        descs_l = sorted(d for d in descs if f"{goal}\n{d}" not in cache)
        if descs_l:
            sys.stderr.write(f"goal: {goal[:70]}... {len(descs_l)} unique elements\n")
            for d, r in zip(descs_l, classify(goal, descs_l), strict=True):
                cache[f"{goal}\n{d}"] = r
        for d in descs:
            verdict[(goal, d)] = cache[f"{goal}\n{d}"]
    if cache_path:
        cache_path.write_text(json.dumps(cache))

    policies = {
        "strict  (helpful only)": lambda r: r["label"] == LABELS[0],
        f"recipe  (helpful or conf<{args.keep_below})": lambda r: (
            r["label"] == LABELS[0] or (r["confidence"] or 0) < args.keep_below
        ),
        "loose   (not 'unrelated'@>=0.95)": lambda r: not (r["label"] == LABELS[1] and (r["confidence"] or 0) >= 0.95),
    }
    print(
        f"{len(events)} plan calls, {sum(len(c) for _, c, _, _, _ in events)} candidates, "
        f"{len(verdict)} unique elements classified\n"
    )
    print(f"{'policy':40s} {'recall(top)':>12s} {'recall(acted)':>14s} {'kept':>7s} {'tokens':>8s}")
    for name, keep in policies.items():
        top_ok = top_n = act_ok = act_n = 0
        kept = total = 0
        tok_before = tok_after = 0.0
        losses: list[str] = []
        for goal, cands, chosen, top, tok in events:
            surv = set()
            n_el = 0
            for c in cands:
                d = c["action"]
                if is_meta(d):
                    surv.add(c["id"])
                    continue
                n_el += 1
                total += 1
                if keep(verdict[(goal, d)]):
                    surv.add(c["id"])
                    kept += 1
            n_surv_el = len([c for c in cands if c["id"] in surv and not is_meta(c["action"])])
            if n_el:
                tok_before += tok
                tok_after += tok * (n_surv_el / n_el)
            if top and top != "none":  # "none" is the meta choice, never a filtered element
                top_n += 1
                if top in surv:
                    top_ok += 1
                else:
                    losses.append(next((c["action"][:90] for c in cands if c["id"] == top), top))
            if chosen:
                act_n += 1
                act_ok += chosen in surv
        print(
            f"{name:40s} {top_ok:4d}/{top_n:<7d} {act_ok:4d}/{act_n:<9d} {kept / total:6.0%} {1 - tok_after / tok_before:7.0%} saved"
        )
        for d in losses[:6]:
            print(f"    lost: {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
