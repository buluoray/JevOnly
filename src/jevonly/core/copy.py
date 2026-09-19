"""Closed-choice page-value extraction and copy-register helpers."""

from .questions import q_collapse, q_goal_clause, q_narrow
from .text import split_parts, unit_spans


def collapse_pick(jev_fn, state, units, purpose, max_rounds=6, exclude=(), trace=None, prefilter=None):
    """Narrow the page text down to one value by repeated choice: at most COLLAPSE_FANOUT parts per round, the
    chosen part split again, until a single line, whose pieces (regex spans + words + the line itself) are the
    last round. Long text is never pre-cut: the model sees it whole and points, the code only slices what it
    pointed at. `exclude` are pieces already rejected: taken off the last round only, because the right value
    often shares its line with the wrong one ("937 feet (286 m) and was completed in 1985"). `trace`, when a
    list is given, receives one record per round -- the options Jev saw, each with its probability, and the
    pick -- so the UI can show how the text was narrowed down. `prefilter(units) -> {"kept", "scores", ...}
    | None` may shrink and reorder the haystack first (one batch classification); `None` means the full
    haystack is used, and the shrink is written to the trace as a round of kind "prefilter".
    Returns {"text", "unit", "rounds"} or None."""
    cur = list(units)
    rounds = 0
    excl = {e.lower() for e in exclude}

    def note(kind, crit, ans):
        if trace is not None:
            probs = ans.get("probabilities", {})
            trace.append(
                {
                    "kind": kind,
                    "pick": ans["choice"],
                    "options": [
                        {"id": k, "text": (v if kind == "parts" else k)[:160], "p": round(probs.get(k, 0.0), 3)}
                        for k, v in crit.items()
                    ],
                }
            )

    if prefilter is not None and len(cur) > 1:
        ranked = prefilter(cur)
        if ranked and ranked.get("kept"):
            kept = list(ranked["kept"])
            if trace is not None:
                scores = ranked.get("scores", {})
                trace.append(
                    {
                        "kind": "prefilter",
                        "pick": f"{len(kept)} of {len(cur)} lines kept"
                        + (f" ({ranked['model']})" if ranked.get("model") else ""),
                        "options": [
                            {"id": u[:40], "text": u[:160], "p": round(scores.get(u, 0.0), 3)} for u in kept[:8]
                        ],
                    }
                )
            cur = kept

    while cur and rounds < max_rounds:
        rounds += 1
        if len(cur) == 1:
            unit = cur[0]
            pieces = unit_spans(unit)
            for w in unit.split():
                w = w.strip(" .,;:()[]\"'")
                if len(w) >= 2 and w.lower() not in {p.lower() for p in pieces}:
                    pieces.append(w)
            if unit.lower() not in {p.lower() for p in pieces}:
                pieces.append(unit)
            pieces = [p for p in pieces if p.lower() not in excl][:24]
            if not pieces:
                return None
            q = q_collapse(pieces, purpose, final=True)
            # the line the pieces were cut from travels with the question, so the pick is judged in context
            ans = jev_fn({**state, "line": unit}, q, "copy")["pick"]
            note("pieces", {k: k for k in pieces}, ans)
            pick = ans["choice"]
            if pick == "none":
                return None
            return {"text": pick, "unit": unit, "rounds": rounds, "p": ans.get("probabilities", {}).get(pick, 0.0)}
        groups = split_parts(cur)
        q = q_collapse(groups, purpose)
        ans = jev_fn(state, q, "copy")["pick"]
        note("parts", {k: v for k, v in q["pick"]["criteria"].items() if k != "none"}, ans)
        pick = ans["choice"]
        if pick == "none" or not pick.startswith("part "):
            return None
        idx = int(pick.split()[1]) - 1
        if not (0 <= idx < len(groups)):
            return None
        cur = groups[idx]
        if len(cur) > 1 and sum(len(u) for u in cur) <= 200:
            cur = [" ".join(cur)]  # a few short units: one line is easier to pick from than parts of one word each
    return None


def narrow_pick(jev_fn, state, units, purpose, trace=None, exclude=(), cap=16):
    """Binary-search-like narrowing where Jev decides when to stop. Round one picks the unit (a goal clause)
    the value is in. Every later round shows the current stretch of words as a window and the moves that
    slide one of its ends inward by 1, 2, 4, 8 ... words -- plus `all`: stop, this exact text is the value.
    No round count is decided by code: the text only ever gets shorter, so the search ends on its own; `cap`
    is a safety bound, not a schedule. Returns {"text", "unit", "rounds", "p"} or None."""
    excl = {e.lower() for e in exclude}
    rounds = 0

    def note(kind, crit, ans):
        if trace is not None:
            probs = ans.get("probabilities", {})
            trace.append(
                {
                    "kind": kind,
                    "pick": ans["choice"],
                    "options": [
                        {"id": k, "text": str(v)[:160], "p": round(probs.get(k, 0.0), 3)} for k, v in crit.items()
                    ],
                }
            )

    units = [u for u in units if u and u.strip()]
    if not units:
        return None
    if len(units) == 1:
        unit = units[0]
    else:
        rounds += 1
        # the units are the goal's clauses: one per choice, asked in the goal's own terms
        qq = q_goal_clause(units[:12], purpose)
        ans = jev_fn(state, qq, "copy")["pick"]
        note("parts", {k: v for k, v in qq["pick"]["criteria"].items() if k != "none"}, ans)
        pick = ans["choice"]
        if pick == "none" or not pick.startswith("part "):
            return None
        idx = int(pick.split()[1]) - 1
        if not (0 <= idx < len(units[:12])):
            return None
        unit = units[idx]
    words = unit.split()
    while words and rounds < cap:
        rounds += 1
        cur = " ".join(words).strip(" .,;:!?")
        if len(words) == 1:
            return None if cur.lower() in excl else {"text": cur, "unit": unit, "rounds": rounds, "p": 1.0}
        moves = {} if cur.lower() in excl else {"all": cur}
        n = len(words)
        # A sliding window with two ends: each move slides ONE end inward by 1, 2, 4, 8 ... words and shows
        # the text that would remain. Nothing ever cuts through the middle, so a value sitting mid-clause
        # is reached by shortening from both sides; the geometric steps keep that to a few rounds.
        k = 1
        while k < n:
            moves[f"head_off_{k}"] = " ".join(words[k:])
            moves[f"tail_off_{k}"] = " ".join(words[:-k])
            k *= 2
        moves = {k2: v.strip(" .,;:!?") for k2, v in moves.items()}
        ans = jev_fn({**state, "text": cur}, q_narrow(moves, purpose), "copy")["pick"]
        note("narrow", moves, ans)
        pick = ans["choice"]
        if pick == "none":
            return None
        if pick == "all":
            return {"text": cur, "unit": unit, "rounds": rounds, "p": ans.get("probabilities", {}).get("all", 0.0)}
        if pick.startswith("head_off_"):
            words = words[int(pick.rsplit("_", 1)[1]) :]
        elif pick.startswith("tail_off_"):
            words = words[: -int(pick.rsplit("_", 1)[1])]
        else:
            return None
    return None
