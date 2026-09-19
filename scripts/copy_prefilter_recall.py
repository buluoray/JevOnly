"""Offline check: would a classifier.dev pre-filter over the page text help the copy step?

Replays ``copy_trace`` events that carry ``units`` (the page text lines the collapse
picked from) and ``picked_unit`` (the line the accepted value came from). Every
unit is classified once as ``shows the value asked for`` / ``does not``, with the
clause (``wanted``) in the instructions. Then, per trace:

  found     -- the picked unit survives the filter (recall; the thing that matters)
  rank      -- where the picked unit sits when units are sorted by that score
  kept      -- share of units surviving, i.e. how much smaller the haystack gets
  rounds    -- how many collapse rounds Jev actually needed on the full haystack

    python scripts/copy_prefilter_recall.py ~/.kiro/crew/uploads/cron_jevonly-*.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

API = "https://classifier.dev"
LABELS = ["shows the value asked for", "does not show it"]


def classify(wanted: str, goal: str, units: list[str]) -> list[dict]:
    out: list[dict] = []
    for start in range(0, len(units), 1000):
        body = json.dumps(
            {
                "labels": LABELS,
                "inputs": units[start : start + 1000],
                "instructions": (
                    "Each text is one line of a web page. 'shows the value asked for' means the line itself "
                    f"contains the value this clause of the goal wants read: `{wanted}`. Goal: {goal} "
                    "When in doubt, keep it."
                ),
            }
        ).encode()
        req = urllib.request.Request(
            API, data=body, headers={"content-type": "application/json", "user-agent": "jevonly-copy-prefilter/1.0"}
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            out.extend(json.load(resp)["results"])
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--keep-below", type=float, default=0.8)
    parser.add_argument("--cache", default="")
    args = parser.parse_args(argv)

    cache_path = Path(args.cache) if args.cache else None
    cache: dict[str, dict] = json.loads(cache_path.read_text()) if cache_path and cache_path.exists() else {}

    rows = []
    for path in args.logs:
        goal = ""
        for line in path.open(encoding="utf-8"):
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("kind") == "start":
                goal = e.get("goal", "")
            if e.get("kind") != "copy_trace" or not e.get("units"):
                continue
            wanted = e.get("wanted") or "(any value the goal asks for)"
            units = e["units"]
            key = lambda u, _w=wanted, _g=goal: f"{_w}\n{_g}\n{u}"  # noqa: E731
            todo = [u for u in units if key(u) not in cache]
            if todo:
                sys.stderr.write(f"{path.name[-26:]} step {e['step']}: classifying {len(todo)} units\n")
                for u, r in zip(todo, classify(wanted, goal, todo), strict=True):
                    cache[key(u)] = r
            verdicts = [cache[key(u)] for u in units]
            score = lambda r: r["scores"].get(LABELS[0], 0.0) if r.get("scores") else 0.0  # noqa: E731
            keep = [r["label"] == LABELS[0] or (r["confidence"] or 0) < args.keep_below for r in verdicts]
            picked = e.get("picked_unit")
            accepted = any(a.get("accepted") for a in e.get("attempts", []))
            rounds = sum(len(a.get("rounds", [])) for a in e.get("attempts", []))
            found = rank = None
            # the collapse may join a few short units into one line, so the picked line maps to every
            # unit it contains; the pick is found when any of those survives, ranked by the best one
            if picked:
                hits = [j for j, u in enumerate(units) if u == picked or (len(u) >= 4 and u in picked)]
                if hits:
                    found = any(keep[j] for j in hits)
                    order = sorted(range(len(units)), key=lambda j: -score(verdicts[j]))
                    rank = min(order.index(j) for j in hits) + 1
            rows.append(
                {
                    "log": path.name[-26:],
                    "step": e["step"],
                    "wanted": wanted[:40],
                    "units": len(units),
                    "kept": sum(keep),
                    "accepted": accepted,
                    "rounds": rounds,
                    "found": found,
                    "rank": rank,
                    "picked": (picked or "")[:70],
                }
            )
    if cache_path:
        cache_path.write_text(json.dumps(cache))
    if not rows:
        print("no copy_trace events with units found (logs must come from the patched loop)", file=sys.stderr)
        return 1

    print(
        f"{'log':26s} {'st':>3s} {'wanted':40s} {'units':>5s} {'kept':>5s} {'acc':>3s} {'rnd':>3s} {'found':>5s} {'rank':>4s}  picked line"
    )
    for r in rows:
        f = "-" if r["found"] is None else ("yes" if r["found"] else "NO")
        rk = "-" if r["rank"] is None else str(r["rank"])
        print(
            f"{r['log']:26s} {r['step']:3d} {r['wanted']:40s} {r['units']:5d} {r['kept']:5d} "
            f"{'y' if r['accepted'] else '.':>3s} {r['rounds']:3d} {f:>5s} {rk:>4s}  {r['picked']}"
        )
    with_pick = [r for r in rows if r["found"] is not None]
    if with_pick:
        n_found = sum(1 for r in with_pick if r["found"])
        kept = sum(r["kept"] for r in rows) / max(1, sum(r["units"] for r in rows))
        top3 = sum(1 for r in with_pick if r["rank"] and r["rank"] <= 3)
        print(
            f"\nrecall {n_found}/{len(with_pick)} picked lines survive the filter; "
            f"haystack kept {kept:.0%}; picked line ranked top-3 by score in {top3}/{len(with_pick)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
