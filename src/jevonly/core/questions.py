"""Closed-choice and probability question templates sent to Jev."""

import re

KEYBOARD_KEYS = list("abcdefghijklmnopqrstuvwxyz0123456789") + [" ", "-", ".", ",", "/", "@"]


def q_next(cands):
    crit = {c["id"]: c["desc"] for c in cands}
    crit["none"] = (
        "No listed action should be taken next: the task is already complete, or this state cannot advance it."
    )
    return {
        "target": {
            "type": "choice",
            "instructions": (
                "An agent is working toward `task_goal` using the information in `facts`, has already done `actions_so_far`, "
                "and observes `state`. Which candidate action should it take NEXT? Read each candidate's description carefully; "
                "several may look alike. Complete every required part of a form or structure before submitting or finishing."
            ),
            "criteria": crit,
        }
    }


def q_bind(cand, facts):
    crit = {k: f"use the fact '{k}' = {str(v)[:120]}" for k, v in facts.items()}
    crit["none"] = "No fact fits; leave it."
    return {
        "fact": {
            "type": "choice",
            "instructions": (
                f"The agent is about to perform: {cand['desc']} — while pursuing `task_goal`, having done `actions_so_far`. "
                "Which fact should be used as the value? Facts already used for this same target are not offered."
            ),
            "criteria": crit,
        }
    }


def q_option(cand):
    crit = {o: f"choose the option '{o}'" for o in cand["options"][:12]}
    return {
        "option": {
            "type": "choice",
            "instructions": (
                f"The agent is about to perform: {cand['desc']} — while pursuing `task_goal` with `facts`. Which option satisfies the goal?"
            ),
            "criteria": crit,
        }
    }


def q_commit(cand):
    return {
        "commit": {
            "type": "choice",
            "instructions": (f"After typing into {cand['desc']} in `state`, how should the agent commit the text?"),
            "criteria": {
                "press_enter": "Press Enter right away: this field submits on Enter (a search box, a single-line entry with no visible submit button for it).",
                "leave_it": "Just leave the typed text: it is one field of a larger form that is submitted later with a button.",
            },
        }
    }


COLLAPSE_PREVIEW_BUDGET = 8000  # characters of preview per parts round, shared by the parts


def part_preview(units, budget):
    """What Jev sees of one part: the whole text when it fits the budget, otherwise the start of EVERY line
    in it. On the Seattle list the first 220 characters of the part holding "Columbia Center 933 ft (284
    m)" read "Development of new high-rises slowed down..." and Jev, honestly, answered none; a line-by-line
    excerpt shows the row's name."""
    whole = " ".join(units)
    if len(whole) <= budget:
        return whole
    per = max(24, budget // max(1, len(units)))
    return " | ".join(u[:per].strip() for u in units)[:budget]


def q_collapse(groups, purpose, final=False):
    """One collapse round. Not final: the choices are parts of the page text, pick the one that CONTAINS the
    value. Final: the choices are the pieces of one line, pick the value itself."""
    if final:
        crit = {s: f"copy `{s}`" for s in groups}
        crit["none"] = "none of these is the value"
        q = (
            "The choices are the pieces of one line of the page. Which piece IS the value -- the smallest one that is exactly the "
            "value wanted, not the words around it? Match the KIND of value the goal asks for (what it measures or names, its unit "
            "or format); another value of a different kind on the same line is not it."
        )
    else:
        crit = {
            f"part {i + 1}": part_preview(g, COLLAPSE_PREVIEW_BUDGET // max(1, len(groups)))
            for i, g in enumerate(groups)
        }
        partial = any(len(" ".join(g)) > len(crit[f"part {i + 1}"]) for i, g in enumerate(groups))
        crit["none"] = "the value is in none of these parts" + (
            " -- judged from the excerpts; pick the part whose excerpts fit best when in doubt" if partial else ""
        )
        q = (
            "The choices are consecutive parts of the page's text"
            + (
                ", each shown as a sparse excerpt: the start of every line in it, separated by ' | '. A value sits on one of "
                "those lines, past the excerpt; pick the part whose lines are about the right thing."
                if partial
                else "."
            )
            + " Which part CONTAINS the value? (It will be narrowed down further.)"
        )
    return {
        "pick": {
            "type": "choice",
            "instructions": (
                f"The agent pursuing `task_goal` is locating a value shown on the current page {purpose}. " + q
            ),
            "criteria": crit,
        }
    }


def q_copy_target(clauses):
    """Before a copy: WHICH part of the goal is the value being copied for. The choices are the goal's own
    clauses, verbatim; the pick is handed to the collapse as its purpose and to the check afterwards."""
    crit = {c: f"the value that `{c}` calls for" for c in clauses}
    crit["none"] = "no part of the goal calls for a value from this page right now"
    return {
        "wanted": {
            "type": "choice",
            "instructions": (
                "An agent pursuing `task_goal` (with `facts` in hand, including values already copied, and `actions_so_far` done) is "
                "about to copy a value off the current `state`. Which part of the goal is that value FOR -- the part not yet satisfied "
                "that this page can answer? The choices are quoted from the goal verbatim."
            ),
            "criteria": crit,
        }
    }


Q_COPY_OK = {
    "answers_question": {
        "type": "noul",
        "instructions": (
            "An agent pursuing `task_goal` copied `value_copied` off a page line reading `copied_from`, meaning it to be the value "
            "that `wanted` calls for. Is `value_copied` that value?"
        ),
        "criteria": {
            "true": "It is: the kind of value `wanted` asks for (what it measures or names, its unit or format), about the subject "
            "`wanted` refers to, and complete enough to use later.",
            "false": "It is not: a value of another kind that shares the line, a value about a different subject on the page, a "
            "fragment of the value, or a label or heading rather than the value itself.",
        },
    }
}


def q_value(cand, spans):
    """Keyboard mode, first question per field: WHICH piece of the goal goes into this field. Deciding the
    value once, up front, is what makes the per-key question stable: asked to pick the next character
    with only the goal to go on, the model re-derived the value at every key and the site's suggestions
    ("Hong Kong", "Paris") pulled it about -- p=0.34 on the second letter of HND, 0.47 on PEK."""
    crit = {s: f"type `{s}` into the field" for s in spans}
    crit["on_page"] = (
        "the value this field needs is not in the goal but is SHOWN on the current page: read it off the page first, then type it"
    )
    crit["none"] = (
        "none of the pieces listed here is the value: it is a longer stretch of the goal's words (the next question "
        "narrows the goal down to it), or nothing in the goal or on the page belongs in this field"
    )
    return {
        "value": {
            "type": "choice",
            "instructions": (
                f"The agent is about to type into {cand['desc']} to pursue `task_goal`. `form_fields` are the other fields and what they hold. "
                "Which piece of the goal is the value THIS field asks for? The choices are quoted from the goal verbatim; pick the one that "
                "belongs in this field, 'on_page' if the value is visible on the page (`page_text`) rather than in the goal, or 'none'."
            ),
            "criteria": crit,
        }
    }


def q_spell_step(cand, remaining):
    """Keyboard mode with the value known: the code spells, the model decides how far to go before looking
    again -- the next word, everything, or stop because the site's autocomplete already offers the goal's
    item (the loop clicks it next). One judgment per word instead of per character: "Grand Hyatt Hawaii
    Expedia" took 27 judgments and 20 s character by character, for the same result."""
    match = re.match(r"\s*\S+\s?", remaining)
    word, rest = (match.group(0) if match else remaining), remaining
    return {
        "spell": {
            "type": "choice",
            "instructions": (
                f"The agent is typing `value_to_type` into {cand['desc']} to pursue `task_goal`; `typed_so_far` is in the field (caret `|`) "
                f"and `remaining_to_type` is what is left. `visible_suggestions` are the site's autocomplete entries for the text so far -- "
                "the SITE's guesses, not the goal. How should the agent continue?"
            ),
            "criteria": {
                "word": f"type the next word, {word.strip()!r}, then look at the suggestions again",
                "all": f"type all of the remaining text, {rest!r}, in one go",
                "done": "stop typing now: one of `visible_suggestions` names exactly the item the goal asks for (not merely a "
                "similar or shorter one), so it can be clicked instead of typing the rest",
            },
        }
    }


def q_key(cand, typed, options, value=None):
    """Keyboard mode: the value has to come from `task_goal` itself, one key at a time. The state carries what
    is already in the field (with the caret marked) and the site's own autocomplete suggestions, so 'stop
    typing' can be chosen as soon as a suggestion matches the goal (the loop then offers those suggestions
    as click candidates on the next step). With `value_to_type` known (see q_value) the question is spelling
    only."""
    crit = {
        k if k != " " else "space": (f"type the character '{k}'" if k != " " else "type a space") for k in KEYBOARD_KEYS
    }
    crit["backspace"] = "delete the character just BEFORE the caret `|`"
    crit["left"] = "move the caret one character to the left (no text changes)"
    crit["right"] = "move the caret one character to the right (no text changes)"
    crit["done"] = (
        "stop typing, because EITHER the field now reads the whole value (letter case does not matter) "
        "OR one of `visible_suggestions` already names exactly what the goal needs"
    )
    if value:
        what = (
            "`value_to_type` is the exact text this field should end up containing, quoted from the goal. `remaining_to_type` is the "
            "part of it not yet in the field: the next key is normally its FIRST character (a blank is 'space'); when it is empty the "
            "value is complete. If `field_matches_value_so_far` is false, the text after `matching_prefix` is wrong and must be deleted "
            "with backspace before continuing. Letters are typed in lower case; case never matters. Which key should be pressed NEXT? "
        )
    else:
        what = "Which key should be pressed NEXT? Spell the value the goal calls for, one character at a time. "
    return {
        "key": {
            "type": "choice",
            "instructions": (
                f"The agent is typing into {cand['desc']} to pursue `task_goal`. `typed_so_far` is the text already in the field, with the caret "
                "shown as `|` (a span in `[...]` is selected and would be replaced by the next character; a bare `|` means the field is EMPTY, "
                "whatever its name or the page says); typed characters go in at the caret. "
                "`visible_suggestions` are the autocomplete entries the site shows for the text so far: they are the SITE's guesses, not the "
                "goal -- a suggestion only counts when it names what the goal asks for. "
                + what
                + "Choose 'done' as soon as the text is complete or such a suggestion is visible."
            ),
            "criteria": crit,
        }
    }


Q_VERIFY = {
    "answers_question": {
        "type": "noul",
        "instructions": (
            "An agent performed `action_taken` as part of `task_goal`. Comparing `state_before` and `state_after`, did the action have its intended effect?"
        ),
        "criteria": {
            "true": "The state changed in the way the action calls for: the expected navigation or command effect happened, the chosen option or typed value now shows, the expected file/content/output appeared. This also counts when the control itself is GONE from `state_after` because using it consumed it, and the effect shows somewhere else instead: a menu or dialog that closed because the choice was taken, a control whose own name now reports the chosen value, a list that changed to match the choice. It ALSO counts when the action's whole purpose was to REMOVE something -- delete, clear, cancel, discard, close -- and the item the action names is now absent from `state_after`: for such an action the absence IS the intended effect. Judge only whether the action did what it says; whether it was the right thing to do for the goal is a different question.",
            "false": "Nothing relevant changed, a different value than intended is shown, an error appeared, or the state moved somewhere the action did not intend. An item the action was meant to remove is still present.",
        },
    }
}


Q_OFFPATH = {
    "answers_question": {
        "type": "noul",
        "instructions": (
            "An agent pursuing `task_goal` has done `actions_so_far` and now observes `state`. Is this state OFF the task's path, i.e. a place from which continuing forward cannot advance the goal, so the agent should go back / undo?"
        ),
        "criteria": {
            "true": "The state is unrelated to the goal (a promotional page, a wrong directory or section, an error page) or has lost the progress made so far, and nothing here moves the goal forward.",
            "false": "The state is on the path: the starting point, intermediate results, the form or workspace being filled, or the end state the goal asks for.",
        },
    }
}


Q_DONE = {
    "answers_question": {
        "type": "noul",
        "instructions": (
            "An agent pursuing `task_goal` has done `actions_so_far` and now observes `state`. Check every part of the goal against the history and the state: is the task fully complete, so the agent should stop?"
        ),
        "criteria": {
            "true": "Every part of the goal has been carried out (per the history) and the state shows the end condition the goal describes.",
            "false": "Some part of the goal is still pending: a form filled but not submitted, a required file or step missing, an intermediate state, or a goal step absent from the history.",
        },
    }
}


Q_PROGRESS = {
    "answers_question": {
        "type": "noul",
        "instructions": (
            "An agent performed `action_taken` as part of `task_goal`, having already done `actions_so_far`. Comparing `state_before` and `state_after`: did this step move the task CLOSER to completion, as opposed to merely looking around, navigating, or re-doing something already done?"
        ),
        "criteria": {
            "true": "A required part of the goal is now done or nearer (a needed value entered, a needed page/section reached for the first time, a required item created or changed).",
            "false": "Nothing the goal requires advanced: the step only read or listed something, moved to a place already visited, undid progress, or repeated an already-completed part.",
        },
    }
}


Q_RISK = {
    "answers_question": {
        "type": "noul",
        "instructions": (
            "In the context of `state`, would performing `candidate_action` COMMIT something that cannot be undone by simply navigating back or editing a field again?"
        ),
        "criteria": {
            "true": "It has an effect that persists beyond this page: it creates, changes or removes data held by a server; moves money or enters a commitment; sends a message or publishes content; grants, changes or ends access or identity (logging in or out, permissions); or moves a process to a stage that cannot be returned from.",
            "false": "It only changes what is shown: navigates, opens or closes a view, sorts or filters, enters or selects a value in a form that is submitted later, or reads something.",
        },
    }
}


Q_VERIFY3 = {
    "result": {
        "type": "choice",
        "instructions": (
            "An agent performed `action_taken` as part of `task_goal`. Comparing `state_before` and `state_after`, what happened?"
        ),
        "criteria": {
            "succeeded": "The state changed in the way the action calls for: the expected navigation or command effect happened, the chosen option or typed value now shows, the expected content or output appeared. This also counts when the control itself is GONE from `state_after` because using it consumed it, and the effect shows somewhere else instead: a menu or dialog that closed because the choice was taken, a control whose own name now reports the chosen value, a list that changed to match the choice.",
            "failed": "Nothing relevant changed, a different value than intended is shown, an error appeared, or the state moved somewhere the action did not intend.",
            "unclear": "The evidence does not settle it: the visible state is ambiguous, the effect may be delayed or happen elsewhere, or the two states differ in ways unrelated to the action.",
        },
    }
}


Q_NOT_APPLIED = {
    "answers_question": {
        "type": "noul",
        "instructions": (
            "An agent performed `action_taken`, a committing request that must never be sent twice if the first one landed. "
            "Comparing `state_before` and `state_after`, does the state PROVE the request was never applied at all?"
        ),
        "criteria": {
            "true": "The state proves nothing was applied: everything the request would have created, changed or removed is exactly as it was before, and the response reports a failure that happened before the change (rejected, refused, not found, invalid) or reports success while nothing it claims to have done is present.",
            "false": "The state does not prove that. Something it would have changed HAS changed, part of it landed, the response never came back (a timeout or no acknowledgement, so it may have been applied out of sight), or the state simply does not say either way.",
        },
    }
}


def q_value_clauses(clauses):
    """Before the first step: WHICH clauses of the goal ask for a value to be read off a page (a time, a
    price, a name) rather than something to do. One noul per clause, one request. The answer fixes how
    many values the run has to collect -- only those clauses are ever offered as copy targets, and the
    run may stop once each of them has one."""
    qs = {}
    for i, c in enumerate(clauses):
        qs[f"value_{i}"] = {
            "type": "noul",
            "instructions": (
                f"Goal: `task_goal`. Consider only this part of it: `{c}`. Does this part ask for a VALUE to be read "
                "off a page and reported or reused later (a time, a price, a count, a name, a code), as opposed to an "
                "action to perform or a condition to arrange?"
            ),
            "criteria": {
                "true": "It asks for a piece of information the page will show, which the agent must read and keep.",
                "false": (
                    "It asks for something to be done (click, choose, fill, include, go to) or describes when to stop; no value is to be read for it. "
                    "'Find X', 'open X', 'go to X', 'look up X' name a page or thing to REACH, not a value to read, even when X is a name: "
                    "'find the tallest building in Seattle' is navigation; 'note its height' is the value."
                ),
            },
        }
        # "stop when you can compare the two heights" names no value itself, yet cannot be met without
        # the second height -- which no other clause asks for. Such a clause collects that value.
        qs[f"needs_{i}"] = {
            "type": "noul",
            "instructions": (
                f"Goal: `task_goal`. Consider only this part of it: `{c}`. Does satisfying it require a value that must be "
                "read off a page and that no other part of the goal collects on its own? A comparison needs BOTH figures: "
                "if the other parts only ask for one of them, the second figure is this part's value."
            ),
            "criteria": {
                "true": "Yes: this part can only be met with a figure or fact the other parts do not ask for (e.g. the second of two heights to compare).",
                "false": "No: it is pure navigation or an action, or every value it depends on is already asked for by another part.",
            },
        }
    return qs


def q_narrow(moves, purpose):
    """One round of narrowing a stretch of text down to a value. `moves` maps a move name to the text it
    would leave: `all` (stop here: this exact text is the value), `head_off_N` / `tail_off_N` (slide that end
    of the window inward by N words; N = 1, 2, 4, 8 ...). Jev decides when to stop -- there is no round count;
    the window only ever gets shorter."""
    crit = {}
    for k, txt in moves.items():
        if k == "all":
            crit[k] = f"STOP: exactly this text is the value -- `{txt}`"
        elif k.startswith("head_off"):
            crit[k] = f"drop the first {k.rsplit('_', 1)[1]} word(s), keep `{txt}` and narrow further"
        elif k.startswith("tail_off"):
            crit[k] = f"drop the last {k.rsplit('_', 1)[1]} word(s), keep `{txt}` and narrow further"
        else:
            crit[k] = f"`{txt}`"
    crit["none"] = "no part of this text is the value"
    return {
        "pick": {
            "type": "choice",
            "instructions": (
                f"Narrowing `text` down to the value {purpose}. Pursuing `task_goal`. Stop (`all`) as soon as the text is "
                "exactly the value, no more and no less; otherwise slide the end that still holds words which do not belong "
                "inward -- by as many words as you are sure of (each option shows the text that would remain)."
            ),
            "criteria": crit,
        }
    }


def q_goal_clause(clauses, purpose):
    """First round of narrowing the GOAL (not the page) to a value: which clause of the goal is this field
    about. Worded for the goal -- asked with the page-text wording ("which part of the page contains the
    value") Jev said none to every clause on a real run."""
    crit = {f"part {i + 1}": c[:220] for i, c in enumerate(clauses)}
    crit["none"] = "no clause of the goal says what this field should receive"
    return {
        "pick": {
            "type": "choice",
            "instructions": (
                f"The agent pursuing `task_goal` needs a value {purpose}. The value is not a name, code or date quoted in the goal; it "
                "is a longer stretch of the goal's own words (a search query, a phrase). The choices are the clauses of the goal, "
                "verbatim. Which clause holds the words this field should receive? (It will be narrowed down to them next.)"
            ),
            "criteria": crit,
        }
    }


Q_REGISTER_COMPLETE = {
    "answers_question": {
        "type": "noul",
        "instructions": (
            "An agent pursuing `task_goal` is stopping. `facts` holds every value it copied during the run (copied_N), each "
            "with the goal clause it was read for. Before the run, `unread_clauses` were also judged to ask for a value, but "
            "no value was copied for them. Does the register already hold what the goal asked to read, so the run is complete?"
        ),
        "criteria": {
            "true": "Yes: each unread clause is satisfied by a value already in `facts` (it restates or refers to the same "
            "value under other words, e.g. a stop condition naming the value another clause read), or asks for nothing "
            "that can be read.",
            "false": "No: at least one unread clause asks for a distinct value the goal needs that is not in `facts`.",
        },
    }
}
