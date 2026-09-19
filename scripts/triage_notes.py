"""Triage JevOnly run-log notes with classifier.dev -- without reading them.

Every ``note`` event across the given .jsonl run logs is sent in one batch to
https://classifier.dev (a keyless HTTP API, itself served by Jev) and sorted
into buckets. The script prints a histogram, then only the notes that landed
in a bucket worth a human's attention -- so a run's problems surface without
scrolling 200 notes.

    python scripts/triage_notes.py ~/runs/*.jsonl
    python scripts/triage_notes.py --show harness_bug,wasted_step runs/*.jsonl

Labels are read semantically by the classifier, so they are plain words.
``none of these`` is a real label on purpose: without it every note is forced
into the best-matching bucket (see the classifier.dev docs).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

API = "https://classifier.dev"
LABELS = [
    "value copied from the page",
    "goal text narrowed to a value",
    "field value decided",
    "wasted or skipped step",
    "stop decision",
    "harness bug or crash",
    "none of these",
]
ATTENTION = {"wasted or skipped step", "harness bug or crash"}
INSTRUCTIONS = (
    "These are one-line progress notes from a browser agent's run log. "
    "'wasted or skipped step' means the agent looked again, refused an action, "
    "discarded a copy, or withdrew an action as useless. "
    "'harness bug or crash' means an error, traceback, timeout or something "
    "the code (not the model) did wrong. When in doubt, keep it in a bucket "
    "that a human would want to review."
)


def load_notes(paths: list[Path]) -> list[tuple[str, int | None, str]]:
    notes: list[tuple[str, int | None, str]] = []
    for path in paths:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("kind") != "note":
                    continue
                text = str(event.get("text") or event.get("note") or "").strip()
                if text:
                    notes.append((path.name, event.get("step"), text))
    return notes


def classify(texts: list[str]) -> list[dict]:
    results: list[dict] = []
    for start in range(0, len(texts), 1000):  # API cap per call
        body = json.dumps(
            {"labels": LABELS, "inputs": texts[start : start + 1000], "instructions": INSTRUCTIONS}
        ).encode()
        req = urllib.request.Request(
            API,
            data=body,
            # Python's urllib default UA is blocked at the edge (403) -- send a real one.
            headers={"content-type": "application/json", "user-agent": "jevonly-triage/1.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.load(resp)
        results.extend(payload["results"])
        sys.stderr.write(f"batch {start // 1000 + 1}: {len(payload['results'])} classified by {payload['model']}\n")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="+", type=Path, help="JevOnly .jsonl run logs")
    parser.add_argument(
        "--show",
        default=",".join(sorted(ATTENTION)),
        help="comma-separated labels whose notes are printed (default: the attention buckets)",
    )
    parser.add_argument("--min-confidence", type=float, default=0.0, help="hide printed notes below this confidence")
    args = parser.parse_args(argv)

    notes = load_notes(args.logs)
    if not notes:
        print("no note events found", file=sys.stderr)
        return 1
    results = classify([text for _, _, text in notes])

    histogram = Counter(r["label"] for r in results)
    print(f"{len(notes)} notes from {len(args.logs)} log(s)\n")
    for label in LABELS:
        print(f"{histogram.get(label, 0):5d}  {label}")

    show = {s.strip() for s in args.show.split(",") if s.strip()}
    grouped: dict[str, list[tuple[float, str, int | None, str]]] = defaultdict(list)
    for (name, step, text), r in zip(notes, results, strict=True):
        if r["label"] in show and (r["confidence"] or 0) >= args.min_confidence:
            grouped[r["label"]].append((r["confidence"] or 0, name, step, text))
    for label in LABELS:
        if label not in grouped:
            continue
        print(f"\n== {label} ({len(grouped[label])})")
        for conf, name, step, text in sorted(grouped[label], reverse=True):
            where = f"{name[-28:]}#{step}" if step is not None else name[-28:]
            print(f"  {conf:.2f}  {where:34s} {text[:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
