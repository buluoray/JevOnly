"""Helpers for spelling goal-derived values into browser fields."""

import re

from .copy import collapse_pick, narrow_pick
from .jev import jev
from .questions import Q_COPY_OK, q_spell_step, q_value
from .text import goal_clauses, goal_spans, page_units

MAX_KEYS = 32


def render_typed(typed, cursor=None, sel_end=None):
    """The field text with the caret shown as `|`, or the selected span in `[...]` (typing replaces it).
    The model cannot press the right key without knowing where the caret is: a click had landed it
    mid-text and 26 Backspaces in a row did nothing while the text looked untouched."""
    if cursor is None or not typed:
        return "|" if not typed else typed + "|"
    cursor = max(0, min(int(cursor), len(typed)))
    if sel_end is not None and sel_end > cursor:
        sel_end = min(int(sel_end), len(typed))
        return f"{typed[:cursor]}[{typed[cursor:sel_end]}]{typed[sel_end:]}"
    return f"{typed[:cursor]}|{typed[cursor:]}"


def spelling_hint(typed, cursor, sel_end, value):
    """What the field still needs, computed by code (the model is weak at character arithmetic: with
    `value_to_type` in view it typed g-a-d for "Grand"). The selected span, if any, is what the next
    key replaces, so it does not count as typed. Case never matters. Returns extra state fields."""
    if not value:
        return {}
    eff = typed or ""
    if typed and cursor is not None and sel_end is not None and sel_end > cursor:
        c = max(0, min(int(cursor), len(typed)))
        s = min(int(sel_end), len(typed))
        eff = typed[:c] + typed[s:]
    lo_v, lo_t = value.lower(), eff.lower()
    if lo_v.startswith(lo_t):
        rem = value[len(eff) :]
        return {"remaining_to_type": rem if rem else "", "field_matches_value_so_far": True}
    n = 0
    while n < min(len(lo_v), len(lo_t)) and lo_v[n] == lo_t[n]:
        n += 1
    return {
        "field_matches_value_so_far": False,
        "matching_prefix": eff[:n],
        "mismatch": f"the field text stops matching the value after {n} character(s): the {len(eff) - n} character(s) after that are wrong",
    }


def next_word(remaining):
    """The next chunk of a value: a word and the blank after it ("Grand ")."""
    m = re.match(r"\s*\S+\s?", remaining)
    return m.group(0) if m else remaining


def type_with_keyboard(
    cand,
    obs,
    attempt,
    *,
    env,
    task,
    history,
    emit,
    step,
    base,
    verify_t,
    kb_bad,
    kb_typed,
    kb_value,
    kb_state,
    copied,
    log,
    user_stopped,
    render_history_fn,
):
    """Spell a value into `cand` one key per judgment. Returns the text left in the field, or None if
    nothing was typed. No Enter: the site's suggestions become click candidates on the next step."""
    # The OTHER form fields, with their values. This field is described separately, and only through
    # `typed_so_far`: its snapshot value ("current value: Seattle") went stale the moment typing began,
    # and while it was still in the state the model kept pressing Backspace on an empty field, 31
    # times, trying to delete text that was no longer there.
    form_fields = [
        {"field": e.get("name") or e.get("context", ""), "value": e.get("value", "")}
        for e in obs.get("elements", [])
        if e.get("role") in ("textbox", "combobox", "searchbox") and e.get("id") != cand["id"]
    ]
    field_desc = " | ".join(b for b in cand["desc"].split(" | ") if not b.startswith("current value:"))
    kcand = {**cand, "desc": field_desc}
    had_dialog = bool(obs.get("open_dialog"))
    r = env.keyboard(cand, focus=True)
    # Focusing a date field opens a picker on many sites. The picker's days are click candidates
    # the planner can see; spelling a date into the box next to them is what produced "9", "99",
    # "sea". A picker that just opened is the answer to this field: leave it open and re-plan.
    dlg = env.open_dialog() if hasattr(env, "open_dialog") else None
    picker_hint = None
    if dlg and len(dlg["choices"]) >= 5 and "type to search" not in cand["desc"]:
        # (an autocomplete combobox opening its suggestion overlay is the field working as intended,
        # not a picker -- treating "Where from?"'s overlay as one had the loop re-clicking SFO four times)
        if not had_dialog:
            emit(
                "note",
                step=step,
                attempt=attempt,
                text=f"focusing the field opened a picker with {len(dlg['choices'])} choices -> choose from it instead of typing",
            )
            kb_state["picker_opened"] = True
            env.undo()  # pops the focus frame; the picker stays open for the next plan
            return None
        sample = ", ".join(dlg["choices"][:4])
        picker_hint = f"{len(dlg['choices'])} clickable choices in the open dialog (e.g. {sample}); 'done' with nothing typed lets the next step click one"
    typed, options = r["typed"], r["options"]
    shown = render_typed(typed, r.get("cursor"), r.get("sel_end"))
    # Which piece of the goal this field takes -- asked once per field, before the first key. The
    # spans come from the goal by code; the model only picks. 'none' leaves the per-key question
    # to work from the goal alone, as before.
    tkey = cand["target_key"]
    if tkey not in kb_value:
        spans = [sp for sp in goal_spans(task["goal"]) if sp not in kb_bad.get(tkey, set())]
        kb_value[tkey] = None
        vst = {
            "task_goal": task["goal"],
            "facts": task["facts"],
            "actions_so_far": render_history_fn(history),
            "page_text": " ".join((obs.get("visible_text") or "").split())[:1500],
            "field": field_desc,
            "form_fields": form_fields,
            "typed_so_far": shown,
            **(
                {"already_typed_into_this_field_earlier": list(kb_typed[tkey.split(":", 1)[-1]])}
                if kb_typed.get(tkey.split(":", 1)[-1])
                else {}
            ),
        }
        vans = jev(vst, q_value(kcand, spans), "value")["value"]
        vchoice = vans["choice"]
        vp = vans.get("probabilities", {}).get(vchoice, 0.0)
        if vchoice == "on_page":
            # The value is on the page, not in the goal: copying is a sub-step of typing here. The
            # collapse runs with THIS field as its purpose, the pick is checked against the field,
            # stored in the register (so the done judgment and later fields see it) and typed.
            emit(
                "note",
                step=step,
                attempt=attempt,
                text=f"the value for this field is shown on the page (p={vp:.2f}) -> reading it off the page first",
            )
            field_name = field_desc.split(" | ")[0]
            wanted = f"fill the field {field_name}"
            purpose = f"to type into {field_name}"
            units = page_units(obs, getattr(env, "_snap", None))
            bad_pieces, attempts_trace, got = [], [], None
            for attempt_copy in range(2):
                rounds_trace = []
                got = collapse_pick(
                    jev,
                    {**base, "state": obs, "field": field_desc, "form_fields": form_fields},
                    units,
                    purpose,
                    exclude=bad_pieces,
                    trace=rounds_trace,
                )
                if not got:
                    attempts_trace.append({"rounds": rounds_trace, "text": None, "ok": None, "accepted": False})
                    break
                ok = got.get("p", 0.0)
                if ok >= verify_t:
                    ok = jev(
                        {
                            **base,
                            "value_copied": got["text"],
                            "copied_from": got["unit"][:200],
                            "wanted": wanted,
                            "field": field_desc,
                        },
                        {"ok": Q_COPY_OK["answers_question"]},
                        "copy",
                    )["ok"]["noul"]
                got["ok"] = ok
                attempts_trace.append(
                    {
                        "rounds": rounds_trace,
                        "text": got["text"],
                        "unit": got["unit"][:160],
                        "p": round(got.get("p", 0.0), 3),
                        "ok": round(ok, 3),
                        "accepted": ok >= verify_t,
                    }
                )
                if ok >= verify_t:
                    break
                emit(
                    "note",
                    step=step,
                    attempt=attempt,
                    text=f"{got['text']!r} does not look like the value for {field_name} (p={ok:.2f}) -> discarded"
                    + (", one more look" if attempt_copy == 0 else ""),
                )
                bad_pieces.append(got["text"])
                got = None
            emit("copy_trace", step=step, wanted=wanted, wanted_probs=None, attempts=attempts_trace, threshold=verify_t)
            if got:
                key = f"copied_{len(copied) + 1}"
                got["wanted"] = wanted
                copied[key] = got
                task["facts"][key] = got["text"]
                emit(
                    "copy",
                    step=step,
                    text=got["text"],
                    context=got["unit"][:160],
                    key=key,
                    rounds=got["rounds"],
                    p=round(got.get("p", 0.0), 3),
                    ok=round(got.get("ok", 0.0), 3),
                    wanted=wanted,
                )
                history.append(
                    {
                        "step": step,
                        "action": f'read "{got["text"]}" off the page (from: {got["unit"][:80]}) to type into {field_name}; it is now fact {key}',
                        "side_effect": "none",
                        "verify": round(got.get("ok", 1.0), 2),
                        "progress": 0.5,
                    }
                )
                kb_value[tkey] = got["text"]
            else:
                # Nothing on the page passed for this field: spelling from the goal would type something the
                # goal never said, so the field is left as it is at this state and the plan moves on.
                emit(
                    "note",
                    step=step,
                    attempt=attempt,
                    text=f"nothing on the page was confirmed as the value for {field_name} -> field left as it is",
                )
                if not had_dialog:
                    env.keyboard(key="Escape")
                env.undo()
                kb_state["left_as_is"] = True
                return None
        else:
            emit(
                "note",
                step=step,
                attempt=attempt,
                text=(
                    f"value for this field: `{vchoice}`"
                    if vchoice != "none"
                    else "no short piece of the goal names this field's value -> narrowing the goal text instead"
                )
                + f" (p={vp:.2f})",
            )
            if vchoice != "none":
                kb_value[tkey] = vchoice
            else:
                # None of the short spans fits. Widen, then let Jev narrow: which clause of the goal is this
                # field about, then -- round after round, until it says stop -- which stretch of that clause's
                # words is the value, trimming an end or halving. "tallest building in Portland, Oregon"
                # comes out of that; spelling it letter by letter from the goal produced "p", "proto" and
                # three steps of back-and-forth. Nothing chosen: the field is not for this step.
                field_name = field_desc.split(" | ")[0]
                rounds_trace = []
                got = narrow_pick(
                    jev,
                    vst,
                    goal_clauses(task["goal"]) or [task["goal"]],
                    f"to type into {field_name}",
                    trace=rounds_trace,
                    exclude=kb_bad.get(tkey, set()),
                )
                emit(
                    "copy_trace",
                    step=step,
                    wanted=f"fill the field {field_name}",
                    wanted_probs=None,
                    attempts=[
                        {
                            "rounds": rounds_trace,
                            "text": got["text"] if got else None,
                            "ok": None,
                            "accepted": bool(got),
                        }
                    ],
                    threshold=verify_t,
                    source="goal",
                )
                if got:
                    emit(
                        "note",
                        step=step,
                        attempt=attempt,
                        text=f"narrowed the goal down to the value for this field: `{got['text']}` "
                        f"({got['rounds']} round(s), p={got.get('p', 0.0):.2f})",
                    )
                    kb_value[tkey] = got["text"]
                else:
                    emit(
                        "note",
                        step=step,
                        attempt=attempt,
                        text="no stretch of the goal was chosen for this field -> field left as it is",
                    )
                    # forget the field: with `None` remembered, the next visit skipped the value question
                    # and fell into letter-by-letter typing ("se", done)
                    kb_value.pop(tkey, None)
                    if not had_dialog:
                        env.keyboard(key="Escape")
                    env.undo()
                    kb_state["left_as_is"] = True
                    return None
    value = kb_value[tkey]
    if value and typed.strip() and typed.strip().lower() != value.lower():
        # The field already reads something ("San Francisco") that is not the value ("SFO") letter for
        # letter. Whether that text already satisfies the goal is a judgment; the spelling code would
        # otherwise delete it and retype, and a field that was fine goes round in a toggle.
        rq = {
            "keep": {
                "type": "choice",
                "instructions": (
                    f"The agent pursuing `task_goal` focused {field_desc} to type `value_to_type` into it, but the field already reads "
                    f"`typed_so_far`. Does the current text already satisfy what the goal wants for this field?"
                ),
                "criteria": {
                    "keep": "yes: the current text already means what the goal asks for here (a name for the same place, the same date in another format); leave it",
                    "replace": "no: the current text is a different value, or a default; clear it and type the value",
                },
            }
        }
        kst = {
            "task_goal": task["goal"],
            "field": field_desc,
            "form_fields": form_fields,
            "typed_so_far": shown,
            "value_to_type": value,
        }
        kans = jev(kst, rq, "key")["keep"]
        emit(
            "key",
            step=step,
            attempt=attempt,
            field=cand["desc"][:120],
            choice=("done" if kans["choice"] == "keep" else "replace"),
            p=round(kans.get("probabilities", {}).get(kans["choice"], 0.0), 3),
            typed=shown,
            suggestions=options[:6],
        )
        if kans["choice"] == "keep":
            if not had_dialog:
                env.keyboard(key="Escape")
            env.undo()
            kb_state["left_as_is"] = True
            return None
    pressed = 0
    seen = {}  # rendered field state -> times seen; a state recurring is the model dithering
    dither = False
    while pressed < MAX_KEYS and not user_stopped():
        seen[shown] = seen.get(shown, 0) + 1
        if seen[shown] >= 3:
            emit(
                "note",
                step=step,
                attempt=attempt,
                text=f"the field has read {shown!r} three times during this typing -> the model is going back and forth, stop typing",
            )
            dither = True
            break
        hint = spelling_hint(typed, r.get("cursor"), r.get("sel_end"), value) if value else {}
        chunk = None  # text the code types in one go (value known); None = a single key
        st = {
            "task_goal": task["goal"],
            "facts": task["facts"],
            "actions_so_far": render_history_fn(history),
            "field": field_desc,
            "form_fields": form_fields,
            "typed_so_far": shown,
            "visible_suggestions": options,
            **({"value_to_type": value, **hint} if value else {}),
            **({"picker": picker_hint} if picker_hint else {}),
        }
        if value:
            # Spelling is arithmetic, so the code does it: with the remainder in view the model still
            # typed g-r-d for "Grand". The model keeps the judgment -- stop now because a suggestion
            # already names what the goal wants, or keep spelling.
            if not hint.get("field_matches_value_so_far", True):
                choice, p = "backspace", 1.0
            elif hint.get("remaining_to_type", "") == "":
                eff = typed.strip()
                if "type to search" in cand["desc"] and not options and " " in eff and not kb_state.get("trimmed"):
                    # The whole value is in an autocomplete box and the site offers NOTHING for it: the
                    # value as written is not a thing the site knows ("Paris CDG" -- Google knows "Paris"
                    # and "CDG", not the pair). Trim to the previous word once and let the suggestions
                    # for the shorter text be judged, instead of declaring done into a dead end.
                    kb_state["trimmed"] = True
                    cut = eff.rfind(" ")
                    n_del = len(typed) - cut
                    emit(
                        "note",
                        step=step,
                        attempt=attempt,
                        text=f"no suggestion for {eff!r} -> deleting the last word to see what the site offers for {eff[:cut]!r}",
                    )
                    for _ in range(n_del):
                        r = env.keyboard(key="Backspace")
                    typed, options = r["typed"], r["options"]
                    shown = render_typed(typed, r.get("cursor"), r.get("sel_end"))
                    pressed += 1
                    value = eff[:cut]  # the shortened text is now the value to complete
                    continue
                choice, p = "done", 1.0
            else:
                ans = jev(st, q_spell_step(kcand, hint["remaining_to_type"]), "key")["spell"]
                pick = ans["choice"]
                if pick == "done":
                    choice, p = "done", ans.get("probabilities", {}).get("done", 0.0)
                else:
                    rem = hint["remaining_to_type"]
                    chunk = rem if pick == "all" else next_word(rem)  # typed as one text below
                    choice, p = chunk, ans.get("probabilities", {}).get(pick, 0.0)
        else:
            # No value for this field: nothing to spell. Letter-by-letter picks from the alphabet are the
            # model composing text, which the harness rules out; the field is left as it is.
            emit(
                "note",
                step=step,
                attempt=attempt,
                text="no value chosen for this field -> nothing typed, field left as it is",
            )
            kb_value.pop(tkey, None)
            if not had_dialog:
                env.keyboard(key="Escape")
            env.undo()
            kb_state["left_as_is"] = True
            return None
        emit(
            "key",
            step=step,
            attempt=attempt,
            field=cand["desc"][:120],
            choice=choice,
            p=round(p, 3),
            typed=shown,
            suggestions=options[:6],
        )
        if choice == "done":
            break
        if chunk is not None:
            r = env.keyboard(text=chunk)
        elif choice == "backspace":
            r = env.keyboard(key="Backspace")
        elif choice == "left":
            r = env.keyboard(key="ArrowLeft")
        elif choice == "right":
            r = env.keyboard(key="ArrowRight")
        else:
            r = env.keyboard(text=(" " if choice == "space" else choice))
        typed, options = r["typed"], r["options"]
        shown = render_typed(typed, r.get("cursor"), r.get("sel_end"))
        pressed += 1
    log["keyboard_keys"] = log.get("keyboard_keys", 0) + pressed
    if pressed >= MAX_KEYS or dither:
        # The keyboard is taken away. Whatever is in the field now is the residue of a failed attempt
        # ("ct 21" after 32 Backspaces, "p" after p/Backspace/p/Backspace), not a value: restore the
        # field and report nothing typed, so the step skips this target instead of accepting the residue.
        log["keyboard_capped"] = log.get("keyboard_capped", 0) + 1
        emit(
            "note",
            step=step,
            text=(f"{MAX_KEYS} keys without 'done'" if pressed >= MAX_KEYS else "typing went back and forth")
            + " -> keyboard taken away, field cleared, target skipped",
        )
        # Clear the focused field itself before undo: the element that took the keys can be an
        # overlay input the snapshot never listed (Google Flights), and undo restores only listed
        # ones -- "pg" and "sae" survived an undo and were offered as the field's value next step.
        env.keyboard(key="ControlOrMeta+A")
        env.keyboard(key="Backspace")
        if not had_dialog:
            env.keyboard(key="Escape")
        env.undo()
        return None
    if pressed == 0:
        # 'done' before any key is "leave the field as it is": a skip, not an action. Returning the
        # field's pre-existing text here made the loop record `typed "Tue, Sep 22" key by key` for a
        # step in which nothing was typed, and then accept that no-op as progress. Escape closes
        # whatever the focus click opened, so the next plan sees the page as it was; undo pops the
        # frame the focus click pushed (nothing to restore, the values did not change). Inside a
        # dialog that was already open, Escape would close the picker the next step should use.
        if not had_dialog:
            env.keyboard(key="Escape")
        env.undo()
        kb_state["left_as_is"] = True
        return None
    kb_state["last_read"] = {"field": field_desc[:120], "text": typed, "suggestions": options[:6]}
    return typed if typed.strip() else None
