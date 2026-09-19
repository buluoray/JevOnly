"""Deterministic text segmentation used to construct closed choices."""

import re


def goal_spans(goal, limit=20):
    """Pieces of the goal a field could ask for, quoted verbatim: quoted strings, codes in capitals (SEA,
    PVG), capitalised phrases with their numbers (Grand Hyatt Hawaii, September 29, 2026, Oct 1st),
    bare numbers. Deterministic; the model chooses among them, it never writes one."""
    spans = []
    for m in re.finditer(r'["“]([^"”]{1,60})["”]|\'([^\']{1,60})\'', goal):
        spans.append(m.group(1) or m.group(2))
    for m in re.finditer(r"\b[A-Z][A-Z0-9]{1,5}\b", goal):
        spans.append(m.group(0))
    for m in re.finditer(r"\b[A-Z][a-zA-Z]+(?:(?:,? | )(?:[A-Z][a-zA-Z]*|\d+(?:st|nd|rd|th)?))*", goal):
        s = m.group(0).rstrip(" ,")
        # a lone capitalised word at the start of a sentence is grammar, not a value ("Find", "Stop")
        if " " not in s and (m.start() == 0 or goal[max(0, m.start() - 2) : m.start()] in (". ", "! ", "? ")):
            continue
        spans.append(s)
    for m in re.finditer(r"\b\d+(?:[/.-]\d+)+\b|\b\d{3,}\b", goal):
        spans.append(m.group(0))
    out, seen = [], set()
    for s in spans:
        s = s.strip(" .,;:")
        if len(s) >= 2 and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out[:limit]


def goal_clauses(goal, limit=10):
    """The goal cut into its clauses, verbatim, at punctuation and at 'then' / 'and': the things a goal asks
    for, one per piece ("note its height in meters"). The options for WHICH value a copy is after. A copy
    that did not know what it was looking for took the year a tower was completed for its height."""
    out, seen = [], set()
    # A comma before a year ("December 5, 2026") or a capitalised word ("Portland, Oregon") is inside a
    # name, not between clauses; "Oregon" used to be cut off and lost.
    for c in re.split(r"(?:[;.!?]|,(?!\s*(?:\d{4}\b|[A-Z])))\s+|\s+(?:and then|then|and)\s+", goal):
        c = re.sub(r"^(?:and then|then|and)\s+", "", c.strip(" .,;:"), flags=re.IGNORECASE).strip(" .,;:")
        if len(c.split()) >= 2 and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out[:limit]


SPAN_RE = re.compile(
    r"[$€£¥]\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:per night|/night|total|USD|EUR))?"  # prices
    r"|\b\d{1,2}:\d{2}(?:\s?[APap][Mm])?\b"  # times
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.? \d{1,2}(?:st|nd|rd|th)?(?:,? \d{4})?\b"  # dates
    r"|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"  # numeric dates
    r"|\b[A-Z][A-Z0-9]{3,}\b"  # codes (PNR, flight numbers)
    r"|\b\d[\d,]*(?:\.\d+)?\s?(?:%|km|mi|kg|lb|h|hr|hrs|min|nights?|guests?|adults?|rooms?|stars?)\b"  # numbers with a unit
    r"|\b[A-Z][a-zA-Z'&-]+(?: (?:[A-Z][a-zA-Z'&-]+|of|and|de|&)){1,5}\b"  # capitalised phrases
)

COLLAPSE_FANOUT = 10


def page_units(obs, snap=None):
    """The observed page as short text units, each a plausible thing to copy from: the text nodes on screen
    (viewport mode) or the lines of the page, plus the named controls with their values. Deduplicated,
    in reading order."""
    units, seen = [], set()

    def add(t):
        t = " ".join(str(t or "").split())
        if 2 <= len(t) <= 200 and t.lower() not in seen:
            seen.add(t.lower())
            units.append(t)

    for t in (snap or {}).get("text_units") or []:
        add(t)
    if not units:
        for t in re.split(r"(?<=[.!?])\s+|\s[·|•]\s", obs.get("visible_text") or ""):
            add(t)
    for e in obs.get("elements", []):
        if e.get("value"):
            add(f"{e.get('name', '')}: {e['value']}")
        elif e.get("role") in ("heading", "cell", "link", "option") and e.get("name"):
            add(e["name"])
        # The row a control sits in (a result row, a list item, a table row) is the text that ties a
        # value to its subject: "6:20 PM - 7:05 AM Air France ... $1,634" says whose price that is,
        # where the loose text nodes of the page do not.
        if e.get("context"):
            add(e["context"])
    return units


def unit_spans(unit):
    """Pieces within one unit worth copying on their own, plus the unit itself when it is short."""
    out, seen = [], set()
    for m in SPAN_RE.finditer(unit):
        s = m.group(0).strip(" .,;:")
        if len(s) >= 2 and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    if len(unit) <= 80 and unit.lower() not in seen:
        out.append(unit)
    return out


def split_parts(units, n=COLLAPSE_FANOUT):
    """Consecutive units in at most n groups of roughly equal text length (reading order kept)."""
    if len(units) <= n:
        return [[u] for u in units]
    total = sum(len(u) + 1 for u in units)
    target = total / n
    groups, cur, cur_len = [], [], 0
    for u in units:
        if cur and cur_len + len(u) > target and len(groups) < n - 1:
            groups.append(cur)
            cur, cur_len = [], 0
        cur.append(u)
        cur_len += len(u) + 1
    if cur:
        groups.append(cur)
    return groups
