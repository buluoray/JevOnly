"""Shrink the copy haystack before the collapse, with one batch classification call.

The collapse hands Jev the page ten parts at a time and narrows by choice; on a long list page that took
three to six rounds and still missed (Portland's list of tallest buildings, the Hacker News front page).
`classifier.dev` is the same decision model (its fast tier answers as ``jev-1.x``), reached without a key:
one POST scores every line of the page against the clause being read, and the collapse then starts from
the lines that scored, best first, instead of from the page's reading order.

Contract: ``rank_units`` returns ``None`` whenever it cannot vouch for the result -- network error, timeout,
malformed reply, or a filter that would drop everything -- and the caller falls back to the full haystack.
It never raises. A dropped line is invisible to Jev afterwards, so the keep rule is biased to keeping:
a line survives when it is labelled as showing the value OR the classifier is unsure about it.
"""

from __future__ import annotations

import json
import os
import urllib.request

API = "https://classifier.dev"
LABELS = ("shows the value asked for", "does not show it")
KEEP_BELOW = 0.8  # an "unsure" verdict below this confidence keeps the line
MIN_UNITS = 12  # a haystack this small is cheaper to collapse than to classify
BATCH = 1000  # the API's per-call cap
TIMEOUT_S = 8.0


def enabled(task=None):
    """The switch: off with ``task["prefilter"] is False`` or ``JEVONLY_PREFILTER=0``; on otherwise."""
    if task is not None and task.get("prefilter") is False:
        return False
    return os.environ.get("JEVONLY_PREFILTER", "1") not in ("0", "false", "no", "off")


def _instructions(wanted, goal):
    return (
        "Each text is one line of a web page. 'shows the value asked for' means the line itself contains "
        f"the value this clause of the goal wants read: `{wanted}`. Goal: {goal} When in doubt, keep it."
    )


def _post(inputs, wanted, goal, opener=None):
    body = json.dumps({"labels": list(LABELS), "inputs": inputs, "instructions": _instructions(wanted, goal)}).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "content-type": "application/json",
            "user-agent": "jevonly/1.0 (+https://github.com/buluoray/JevOnly)",
        },
    )
    with (opener or urllib.request.urlopen)(req, timeout=TIMEOUT_S) as resp:
        return json.load(resp)


def rank_units(units, wanted, goal, opener=None):
    """Score ``units`` against ``wanted``; return ``{"kept": [unit, ...], "scores": {unit: p}, "model": str}``
    with ``kept`` best-first, or ``None`` when the caller should use the full haystack instead."""
    units = list(units)
    if len(units) < MIN_UNITS:
        return None
    try:
        results, models = [], set()
        for start in range(0, len(units), BATCH):
            payload = _post(units[start : start + BATCH], wanted, goal, opener)
            results.extend(payload["results"])
            models.add(str(payload.get("model", "")))
        if len(results) != len(units):
            return None
        scores, kept = {}, []
        for unit, r in zip(units, results, strict=True):
            score = (r.get("scores") or {}).get(LABELS[0])
            conf = r.get("confidence")
            scores[unit] = float(score) if score is not None else 0.0
            if r.get("label") == LABELS[0] or conf is None or float(conf) < KEEP_BELOW:
                kept.append(unit)
        if not kept or len(kept) >= len(units):
            return None
        kept.sort(key=lambda u: -scores[u])
        return {"kept": kept, "scores": scores, "model": ",".join(sorted(m for m in models if m))}
    except Exception:  # noqa: BLE001 -- any failure means "no verdict", never a crash in the loop
        return None
