"""Environment-agnostic closed-vote browser search loop."""

import time
import urllib.parse
from contextlib import suppress

from .. import envs
from . import prefilter as _prefilter
from .copy import collapse_pick
from .jev import CALLS, jev
from .keyboard import type_with_keyboard
from .questions import (
    Q_COPY_OK,
    Q_DONE,
    Q_NOT_APPLIED,
    Q_OFFPATH,
    Q_PROGRESS,
    Q_REGISTER_COMPLETE,
    Q_RISK,
    Q_VERIFY,
    Q_VERIFY3,
    q_bind,
    q_commit,
    q_copy_target,
    q_next,
    q_option,
    q_value_clauses,
)
from .text import goal_clauses, page_units

MAX_STEPS = 28
MAX_ATTEMPTS = 3
TASK_BUDGET_S = 150
MAX_BACKTRACKS = 12
MAX_VISITS = 8
STALL_K = 4
EMPTY_PAGE_WAIT_S = 1.5
NONE_LED_FLOOR = 0.25
NEXT_BEST_FLOOR = 0.02
MAX_FEEDBACK = 2
VARIANTS = {
    "std": {"consequences": True},
    "verify3": {"consequences": True, "verify3": True},
    "noaccept": {},
    "base": {},
    "consequences": {"consequences": True},
    "std_kb": {"consequences": True, "keyboard": True},
    "noaccept_kb": {"keyboard": True},
}
DONE_T, VERIFY_T, OFFPATH_T = 0.7, 0.5, 0.6
NONE_T, DONE_WEAK_T = 0.8, 0.35
NONE_STREAK_K = 3
PROGRESS_T = 0.25
MAX_NO_PROGRESS = 3
RISK_T = 0.5
DELAYED_ACK_WAIT_S = 1.5
BORDERLINE_BAND = 0.06
NOT_APPLIED_T = 0.50
EVENT_KINDS = frozenset(
    {
        "act",
        "approval",
        "answer",
        "copy",
        "copy_trace",
        "end",
        "judge",
        "key",
        "note",
        "observe",
        "plan",
        "start",
        "undo",
        "verify",
    }
)


def verify_call(base, desc, obs, after, flags):
    """One verification request. Returns (score, label, progress): Noul p(true) or, in the verify3 variant,
    the mapped Choice -> succeeded=p(succeeded), failed=1-p, unclear=0.5 with the label kept for the log.
    `progress` is asked in the SAME request (same state_before/state_after, which is where the tokens are)
    and only read if the action is accepted -- a speculative head, in TypeSafe's fan-out sense."""
    st = {**base, "action_taken": desc, "state_before": obs, "state_after": after}
    if obs.get("open_dialog") and not after.get("open_dialog"):
        # A fact, not a verdict: the dialog listed in state_before is no longer open. Without it the
        # judgment on a picker's Done button sat right at the threshold (0.44-0.62), because the button's
        # label promised a search and the page after it is the form again.
        st["dialog_change"] = (
            "the dialog that was open in state_before is closed in state_after; the page behind it is listed again"
        )
    elif after.get("open_dialog") and not obs.get("open_dialog"):
        st["dialog_change"] = "a dialog opened; state_after lists its controls"
    if flags.get("verify3"):
        ans = jev(st, {**Q_VERIFY3, "progress": Q_PROGRESS["answers_question"]}, "verify+progress")
        r = ans["result"]
        p = r["probabilities"]
        lab = r["choice"]
        score = (
            p.get("succeeded", 0.0)
            if lab == "succeeded"
            else min(0.49, 1 - p.get("failed", 0.0))
            if lab == "failed"
            else 0.45
        )
        return score, lab, ans["progress"]["noul"]
    ans = jev(
        st, {"verify": Q_VERIFY["answers_question"], "progress": Q_PROGRESS["answers_question"]}, "verify+progress"
    )
    return ans["verify"]["noul"], None, ans["progress"]["noul"]


def render_history(history):
    """Structured history -> the short strings Jev reads. Undone actions are removed, not narrated."""
    if not history:
        return ["(nothing yet)"]
    return [h["action"][:160] + (" (outcome unconfirmed)" if h.get("unconfirmed") else "") for h in history]


def wrong_value(task, right):
    """A plausible wrong fill: another fact's value (the kind of slip a real agent makes), never a literal marker."""
    for v in task["facts"].values():
        if isinstance(v, str) and v != right and 1 <= len(v) <= 40:
            return v
    return "wrong value 123"


def run_task(task, variant="std", rep=0, on_event=None, stop=None):
    """`on_event(kind, payload)` and `stop()` are optional seams for a live viewer: events narrate the
    loop's decisions (observe / plan / act / verify / undo / stop) and `stop()` returning True ends the
    task with stopped="user_stop". Both default to no-ops for library callers."""
    flags = dict(VARIANTS[variant])
    if not task.get("terminal"):
        flags.pop("consequences", None)
    # The decision lines a task may move: `task["thresholds"]` = {verify, offpath, done, risk}. Everything
    # else keeps the module defaults.
    th = task.get("thresholds") or {}
    verify_t = float(th.get("verify", flags.get("verify_t", VERIFY_T)))
    offpath_t = float(th.get("offpath", OFFPATH_T))
    done_t = float(th.get("done", DONE_T))
    risk_t = float(th.get("risk", RISK_T))
    # What happens when Jev judges an action irreversible (risk >= risk_t): "refuse" skips it, "ask" hands
    # it to `task["approve"](info) -> bool` (a viewer prompt, a CLI question) and skips it when that says
    # no or is missing, "allow" performs it. Nothing irreversible runs without one of these saying so.
    irreversible_policy = str(task.get("irreversible") or "refuse")
    approver = task.get("approve")
    # One batch classification before a collapse (same decision model, no key) so a long page is read from its
    # best-scoring lines first; off with task["prefilter"]=False or JEVONLY_PREFILTER=0. Returns the
    # collapse_pick `prefilter` argument for the clause being read, or None when the switch is off.
    prefilter_on = _prefilter.enabled(task)

    def prefilter_for(wanted):
        if not prefilter_on:
            return None
        return lambda units: _prefilter.rank_units(units, wanted or "the value the goal asks to find", task["goal"])

    env = envs.make(task)
    log = {
        "id": task["id"],
        "env": task["env"],
        "variant": variant,
        "rep": rep,
        "steps": [],
        "faults": task["faults"],
        "success": False,
    }
    t0, calls0 = time.monotonic(), len(CALLS)

    def emit(_kind, **payload):
        if on_event is None:
            return
        with suppress(Exception):
            on_event(_kind, payload)

    def shot(idx=None):
        return env.screenshot(idx) if on_event is not None and hasattr(env, "screenshot") else None

    def user_stopped():
        return bool(stop and stop())

    # What the site sees (automation flag, UA, headless / profile / channel): the evidence for "was it
    # the browser's fingerprint that tripped the bot check?" -- read it from the log, not guessed.
    browser_info = None
    if hasattr(env, "_cmd"):
        try:
            pr = env._cmd(cmd="probe")
            browser_info = {k: pr.get(k) for k in ("webdriver", "headless", "profile", "channel", "ua")}
        except Exception:  # noqa: BLE001
            browser_info = None
    emit(
        "start",
        task_id=task["id"],
        goal=task["goal"],
        start=task.get("start"),
        variant=variant,
        browser=browser_info,
        has_code_check=bool(task.get("terminal")),
        max_steps=task.get("max_steps", MAX_STEPS),
        viewport_only=bool(task.get("viewport_only")),
        thresholds={
            "verify": verify_t,
            "offpath": offpath_t,
            "done": done_t,
            "none": NONE_T,
            "done_weak": DONE_WEAK_T,
            "risk": risk_t,
            "not_applied": NOT_APPLIED_T,
            "progress": PROGRESS_T,
        },
    )
    history, used_facts = (
        [],
        {},
    )  # history: [{step, action, side_effect, verify, progress}]; used_facts: target_key -> set(fact keys)
    rejected = {}  # fingerprint -> {cand_id: verify} actions that failed verification here
    banned = set()  # actions withdrawn task-wide (toggles: see the loop head)
    act_failures = {}  # target_key -> action errors (timeouts, detached); three withdraw the target
    pending_fault = None  # a fault whose step could not express it, waiting for one that can
    used_retry_rescue = False  # the one permitted retry of a provably-undelivered committing action
    revisits = {}  # cand_id -> times it landed the task back on a known state
    accepted_fams = []  # (widget family, target, done score at that step) per accepted action, for the stall guard
    kb_failures = {}  # target_key -> abandoned keyboard attempts; the second one withdraws the target
    kb_state = {}  # flags the keyboard helper raises for the loop (picker_opened)
    kb_value = {}  # target_key -> piece of the goal chosen for that field (None = spell from the goal)
    kb_bad = {}  # target_key -> values typed there that failed verification; not offered again
    kb_typed = {}  # target_key -> values typed there and accepted, in order (a reused search box)
    copied = {}  # copied_N -> {"text", "unit", ...}: values read off pages, also entered into task["facts"]
    walled_hosts = set()  # sites that answered a navigation with a block page; their links are not offered again
    copy_dups = {}  # url -> duplicate copies attempted there; two withdraw the copy action on that page
    asked_missing = set()  # urls where the guard asked "which goal clause is still unsatisfied here" (once per page)
    forced_wanted = None  # ...and the clause it named, for the copy it forces next
    copy_failed = {}  # url -> clauses for which nothing on that page was chosen; not offered for copying there again

    # Before the first step: how many values does this goal want read? One request, one noul per clause.
    # "Include nearby airports" and "stop when you can read the time" are things to do; "the price of the
    # earliest flight" is a value. Only value clauses are offered as copy targets, and the run may stop
    # once each has one -- the copied values, not a done score, are what the goal asked for.
    all_clauses = goal_clauses(task["goal"])
    value_clauses = []
    if all_clauses:
        ans = jev({"task_goal": task["goal"]}, q_value_clauses(all_clauses), "plan")
        value_clauses = [
            c
            for i, c in enumerate(all_clauses)
            # `value` is the primary signal; `needs` (the clause needs a value no other clause asked for) is
            # the backstop for "compare the two heights", and is held to a higher bar: at 0.5 it declared
            # "Include nearby airports" a value on the flights goal (0.60), which no page can ever satisfy.
            if ans[f"value_{i}"]["noul"] >= 0.5 or ans.get(f"needs_{i}", {}).get("noul", 0.0) >= 0.7
        ]
        emit(
            "note",
            step=0,
            text=(
                f"the goal asks for {len(value_clauses)} value(s) to be read: "
                + "; ".join(f"`{c}`" for c in value_clauses)
                if value_clauses
                else "no part of the goal asks for a value to be read; the run ends on Jev's done signal"
            )
            + " | per clause (value, needs): "
            + "; ".join(
                f"`{c[:40]}` ({ans[f'value_{i}']['noul']:.2f}, {ans.get(f'needs_{i}', {}).get('noul', 0.0):.2f})"
                for i, c in enumerate(all_clauses)
            ),
        )

    def open_clauses(url):
        """Goal clauses worth asking about on this page: the value clauses (every clause when none was flagged),
        minus those shown to hold no value here. A clause that already has a value stays offered: "note its
        height in meters" collects Seattle's height AND Portland's -- dropping it after the first left the
        Portland list page with only the name clause to copy for, and the second height was never read.
        The same value twice is deduplicated where it is stored."""
        pool = value_clauses or all_clauses
        return [c for c in pool if c not in copy_failed.get(url, set())]

    def values_in_hand():
        return bool(value_clauses) and all(c in {v.get("wanted") for v in copied.values()} for c in value_clauses)

    none_streak = 0  # consecutive plans with 'none' first, whatever the done score (see NONE_STREAK_K)
    last_url = prev_url = (
        None  # the URL observed at this step / the one before (a wall is judged against where we came from)
    )
    accept_sig_at = {}  # cand_id -> acceptance checklist when it was last taken
    last_taken = None  # (fingerprint, cand_id, weak) of the last accepted action, for step-start undo
    visits = {}  # fingerprint -> times observed (cycle detection)
    risk_cache = {}  # candidate description -> p(irreversible)
    fed_back = 0  # times a false 'done' was refused with the acceptance check
    refused_stop = []  # the failing acceptance items shown when a stop was refused
    no_progress = 0  # consecutive accepted steps that did not move the task forward
    step = 0

    def side_effect(cand, obs):
        s = cand.get("side_effect", "unknown")
        if s != "unknown":
            return s
        if cand["desc"] not in risk_cache:
            risk_cache[cand["desc"]] = jev({"state": obs, "candidate_action": cand["desc"]}, Q_RISK, "risk")[
                "answers_question"
            ]["noul"]
        return "irreversible" if risk_cache[cand["desc"]] >= risk_t else "reversible"

    try:
        while step < task.get("max_steps", MAX_STEPS):
            if user_stopped():
                log["stopped"] = "user_stop"
                break
            if time.monotonic() - t0 > task.get("budget_s", TASK_BUDGET_S) or log.get("backtracks", 0) > task.get(
                "max_backtracks", MAX_BACKTRACKS
            ):
                log["stopped"] = "budget_exhausted"
                break
            # A fault named for this step, or one carried forward from an earlier step whose action
            # could not express it (wrong_fill needs a typed value, partial needs a write long enough
            # to halve, and those actions are not on every menu). Carrying it forward is what keeps a
            # specified fault from silently becoming a normal action and quietly shrinking the count.
            fault = task["faults"].get(step) or pending_fault
            pending_fault = None
            if fault and fault["kind"] == "concurrent" and hasattr(env, "concurrent"):
                env.concurrent()  # a third party changes the environment between our steps
                log.setdefault("concurrent_injected", 0)
                log["concurrent_injected"] += 1
                fault = None
            obs = env.observe()
            fp = env.fingerprint(obs)
            prev_url, last_url = last_url, obs.get("url") or ""
            emit(
                "observe",
                step=step,
                url=obs.get("url"),
                title=obs.get("title"),
                n_elements=len(obs.get("elements", [])),
                headings=obs.get("headings"),
                popups=obs.get("popup_messages_from_last_action"),
                screenshot=shot(),
                acceptance=(env.acceptance(obs) if hasattr(env, "acceptance") and task.get("terminal") else None),
            )
            # A toggle looks like this: the action is accepted, it lands the task back on a state it
            # has already been in, AND nothing the goal requires has moved meanwhile. The last clause
            # is what stops the guard eating legitimate navigation: walking back up a directory or
            # reopening a page you have been on returns to a known state too, but it happens either
            # side of real work, so the code-owned acceptance checklist changes across it. A model
            # opinion (`progress`) cannot play this role -- it called reopening a dialog progress.
            if last_taken and visits.get(fp, 0) >= 1:
                sig = tuple(env.acceptance(obs)) if hasattr(env, "acceptance") else ()
                aid = last_taken[1]
                if sig == accept_sig_at.get(aid, object()):
                    revisits[aid] = revisits.get(aid, 0) + 1
                    if revisits[aid] >= 2 and aid not in banned:
                        banned.add(aid)
                        log.setdefault("toggles_withdrawn", []).append(
                            f"action {aid} returned to an already-visited state {revisits[aid]}x with the acceptance "
                            f"checklist unchanged -> withdrawn for the rest of this task"
                        )
                        emit(
                            "note",
                            step=step,
                            text=f"action {aid} keeps returning to a known state with nothing required changed -> withdrawn as a toggle",
                        )
                else:
                    revisits.pop(aid, None)  # real work happened around it; not a toggle
                accept_sig_at[aid] = sig
            visits[fp] = visits.get(fp, 0) + 1
            if visits[fp] > MAX_VISITS:
                log["stopped"] = "cycle"
                log["steps"].append(
                    {"step": step, "fp": fp[:80], "events": [f"state seen {visits[fp]} times -> cycle, stop"]}
                )
                emit("note", step=step, text=f"this state has been seen {visits[fp]} times -> cycle, stop")
                break
            rec = {"step": step, "fp": fp[:80], "events": []}
            base = {"task_goal": task["goal"], "facts": task["facts"], "actions_so_far": render_history(history)}
            if flags.get("consequences"):
                base["acceptance_check"] = env.acceptance(obs)
                if refused_stop:
                    base["stop_was_refused_because"] = refused_stop
            if env.terminal(obs):
                done = jev({**base, "state": obs}, Q_DONE, "done")["answers_question"]["noul"]
                rec.update(done_score=done, terminal_by_code=True)
                log["steps"].append(rec)
                log["success"] = True
                log["done_signal_at_terminal"] = done
                emit(
                    "note",
                    step=step,
                    text=f"the code-owned completion check is satisfied (Jev's own done signal: {done:.2f}) -> success",
                )
                break
            # Candidates first: the planning question rides in the same request as the two judgments
            # below. Everything here is local (no model call), so computing it before a possible undo
            # costs nothing.
            all_cands = env.candidates()
            if step == 0 and not all_cands:
                rec["events"].append("start page exposes no actionable element -> site unavailable")
                log["stopped"] = "site_unavailable"
                log["steps"].append(rec)
                break
            if not all_cands and last_taken is not None:
                # No control at all right after an action. Either the page is still rendering (heavy
                # sites paint late) or it is a wall: a bot check, an error page, "your browser did
                # something unexpected" (Hyatt). Give it two more looks, then treat it as a dead end:
                # go back to where the action was taken, withdraw that action there, and re-plan --
                # ending the run on "no candidates" hid the wall from the log and closed the window.
                for wait_i in range(2):
                    time.sleep(EMPTY_PAGE_WAIT_S)
                    obs = env.observe()
                    fp = env.fingerprint(obs)
                    all_cands = env.candidates()
                    if all_cands:
                        emit(
                            "note",
                            step=step,
                            text=f"the page showed no controls at first; {len(all_cands)} appeared after {EMPTY_PAGE_WAIT_S * (wait_i + 1):.1f}s",
                        )
                        break
                if not all_cands:
                    text = " ".join((obs.get("visible_text") or "").split())[:160]
                    emit(
                        "note",
                        step=step,
                        text=f'the page exposes no controls ("{text}") -> dead end, going back and withdrawing the action that led here',
                    )
                    rec["events"].append("no controls after action -> undo, withdraw, replan")
                    h_now = (urllib.parse.urlsplit(obs.get("url") or "").hostname or "").removeprefix("www.")
                    h_prev = (urllib.parse.urlsplit(prev_url or "").hostname or "").removeprefix("www.")
                    if h_now and h_now != h_prev and h_now not in walled_hosts:
                        walled_hosts.add(h_now)
                        emit(
                            "note",
                            step=step,
                            text=f"{h_now} is treated as a wall for the rest of this run; its links are no longer offered",
                        )
                    env.undo()
                    emit(
                        "undo",
                        step=step,
                        reason="the page offers nothing to act on (a wall or an error page) -> undo the last action and re-plan",
                        screenshot=shot(),
                    )
                    if history and history[-1].get("step") == last_taken[4]:
                        history.pop()
                    rejected.setdefault(last_taken[0], {})[last_taken[1]] = 0.0
                    last_taken = None
                    log["backtracks"] = log.get("backtracks", 0) + 1
                    log["steps"].append(rec)
                    step += 1
                    continue
            dead = rejected.get(fp, {})
            if walled_hosts:
                all_cands = [c for c in all_cands if c.get("host") not in walled_hosts]
            if copy_dups.get(obs.get("url", ""), 0) >= 2:
                all_cands = [c for c in all_cands if c["id"] != "copy"]
            # a chained copy (decided right after the previous copy) is offered even though copying was
            # already used once on this page state
            cands = [
                c
                for c in all_cands
                if (c["id"] not in dead or (forced_wanted and c["id"] == "copy")) and c["id"] not in banned
            ]
            least_bad = None
            if not cands or (dead and len(dead) >= min(MAX_ATTEMPTS, len(all_cands))):
                # Every reasonable alternative here failed verification. A reversible least-bad action may be tried
                # once; an irreversible one is never forced through on failed evidence -- that is an escalation.
                least_bad = max(dead, key=lambda k: dead[k]) if dead else None
                lb = next((c for c in all_cands if c["id"] == least_bad), None)
                if lb is not None and side_effect(lb, obs) == "irreversible":
                    rec["events"].append(
                        f"all alternatives failed and the least-bad one ({least_bad}) is irreversible -> escalate"
                    )
                    log["stopped"] = "escalate_no_safe_action"
                    log["escalated"] = True
                    log["steps"].append(rec)
                    break
                cands = [c for c in all_cands if c["id"] not in banned] or all_cands
            # One request, three heads: done, offpath, next. They read the same page state, and the
            # state (a full element table) is nearly all of the request, so three requests cost three
            # times the tokens for the same information. The judgments are unchanged; only the transport
            # is shared. `done` is not asked before anything has been done.
            qs = {"offpath": Q_OFFPATH["answers_question"]}
            if history:
                qs["done"] = Q_DONE["answers_question"]
            if cands:
                qs["target"] = q_next(cands)["target"]
            for shrink in range(4):
                try:
                    ans_all = jev(
                        {**base, "state": obs, "candidates": [{"id": c["id"], "action": c["desc"]} for c in cands]},
                        qs,
                        "judge+next",
                    )
                    break
                except RuntimeError as exc:
                    # The page state did not fit in one request (a whole-page observation of a long article).
                    # Halve it -- elements, text, context -- for the rest of the run and ask again. A smaller
                    # page listing is a worse observation; a crash is no observation at all.
                    if "max_tokens_exceeded" not in str(exc) or shrink == 3:
                        raise
                    env.shrink_observation()
                    keep = max(20, len(obs["elements"]) // 2)
                    obs["elements"] = obs["elements"][:keep]
                    obs["visible_text"] = obs["visible_text"][: max(500, len(obs["visible_text"]) // 2)]
                    kept = {e["id"] for e in obs["elements"]}
                    cands = [c for c in cands if c["id"] in kept or c["idx"] < 0]
                    if "target" in qs:
                        qs["target"] = q_next(cands)["target"]
                    rec["events"].append(
                        f"page state too large for one request -> observation halved ({keep} elements, text {len(obs['visible_text'])} chars) for the rest of the run"
                    )
                    emit(
                        "note",
                        step=step,
                        text=f"page state too large for one request -> observation halved ({keep} elements) for the rest of the run",
                    )
            done = ans_all["done"]["noul"] if history else 0.0
            rec["done_score"] = done
            if done >= done_t:
                rec["events"].append("jev_said_done_but_code_says_not_terminal")
                log["false_done"] = log.get("false_done", 0) + 1
                if task.get("terminal"):
                    emit("note", step=step, text=f"Jev says done ({done:.2f}) but the code-owned check says not yet")
            off = ans_all["offpath"]["noul"]
            rec["offpath_score"] = off
            emit("judge", step=step, done=done, offpath=off, done_asked=bool(history))
            if off >= offpath_t and last_taken is not None and not last_taken[2]:
                if last_taken[3] == "irreversible":
                    rec["events"].append(f"offpath={off:.2f} after an irreversible action -> cannot undo, escalate")
                    log["stopped"] = "escalate_offpath_after_irreversible"
                    log["escalated"] = True
                    log["steps"].append(rec)
                    break
                rec["events"].append("offpath -> undo, replan")
                env.undo()
                emit(
                    "undo",
                    step=step,
                    reason=f"off the task's path ({off:.2f} >= {offpath_t}) -> undo the last action and re-plan",
                    screenshot=shot(),
                )
                if history and history[-1].get("step") == last_taken[4]:
                    history.pop()  # the undone action leaves the record instead of being narrated
                rejected.setdefault(last_taken[0], {})[last_taken[1]] = 0.0
                last_taken = None
                log["backtracks"] = log.get("backtracks", 0) + 1
                log["steps"].append(rec)
                step += 1
                continue
            if not cands:
                log["steps"].append(rec)
                log["stopped"] = "no_candidates"
                break
            ans = ans_all["target"]
            probs = ans["probabilities"]
            ranked = sorted(probs, key=lambda k: -probs[k])
            rec["top3"] = [(k, round(probs[k], 2)) for k in ranked[:3]]
            _desc = {c["id"]: c["desc"] for c in cands}
            emit(
                "plan",
                step=step,
                n_candidates=len(cands),
                n_dead=len(dead),
                none=round(probs.get("none", 0.0), 3),
                ranked=[
                    {"id": k, "p": round(probs[k], 3), "desc": _desc.get(k, "(no listed action should be taken)")[:200]}
                    for k in ranked[:6]
                ],
            )
            none_streak = none_streak + 1 if ranked[0] == "none" else 0
            copy_cand = next((c for c in all_cands if c["id"] == "copy"), None)
            if forced_wanted and copy_cand is not None:
                # a chained copy decided right after the previous one (see the copy branch). It outranks a
                # weak stop: on the closed-PR list the number was copied, the title was named as the next
                # value, and the very next none-led plan stopped the run with the title never read.
                if copy_cand not in cands:
                    cands.append(copy_cand)
                ranked = ["copy"] + [k for k in ranked if k not in ("none", "copy")]
                probs["copy"] = 1.0
                rec["events"].append(f"chained copy for `{forced_wanted}`")
            page_key = obs.get("url", "")
            if (
                ranked[0] == "none"
                and value_clauses
                and not values_in_hand()
                and copy_cand is not None
                and page_key not in asked_missing
            ):
                # 'none' with values still unread means "no navigation is needed here", not "nothing is
                # left to do": on the GitHub closed-PR list and the sorted Amazon results the values were
                # on screen, copy carried 0.15-0.19 and 'none' 0.75-0.81, and the loop either looked again
                # three times or stopped outright. So the first none-led plan on a page asks the one
                # question the code can act on -- which value clause could this page answer -- and copies
                # for it now. Asked once per page; a clause that yields nothing here is not offered again
                # (copy_failed). A copy FOR ANOTHER CLAUSE is not the copy already used on this page
                # state, so the once-per-state memo does not apply to it.
                asked_missing.add(page_key)
                clauses = open_clauses(page_key)
                if clauses:
                    w = jev({**base, "state": obs}, q_copy_target(clauses), "copy")["wanted"]
                    if w["choice"] != "none":
                        forced_wanted = w["choice"]
                        emit(
                            "note",
                            step=step,
                            text=f"'none' leads (p={probs['none']:.2f}) but `{forced_wanted}` is still unread and this page "
                            f"looks like it shows it (p={w['probabilities'].get(forced_wanted, 0.0):.2f}) -> copying for it first",
                        )
                        rec["events"].append(f"none-led with values missing: copy forced for `{forced_wanted}`")
                        if copy_cand not in cands:
                            cands.append(copy_cand)
                        ranked = ["copy"] + [k for k in ranked if k not in ("none", "copy")]
                        probs["copy"] = 1.0  # code-owned choice: not subject to the noise floor below
                        # The forced copy is the last thing the code can do for the goal on this page. If
                        # 'none' leads the very next plan, that is the stop -- not the first of three more.
                        none_streak = NONE_STREAK_K - 1
            if ranked[0] == "none":
                have_values = values_in_hand()
                if have_values:
                    emit(
                        "note",
                        step=step,
                        text=f"every value the goal asks for is in hand ({len(value_clauses)}) and 'none' leads -> stopping",
                    )
                elif none_streak >= NONE_STREAK_K and probs["none"] < NONE_T:
                    emit(
                        "note",
                        step=step,
                        text=f"'none' has ranked first {none_streak} plans in a row (done {done:.2f}) -> taking that as the stop signal",
                    )
                if (probs["none"] >= NONE_T and done >= DONE_WEAK_T) or none_streak >= NONE_STREAK_K or have_values:
                    if flags.get("consequences") and fed_back < MAX_FEEDBACK:
                        # Jev believes the task is done; the code-owned check disagrees. Refuse the stop, show why, re-plan.
                        fed_back += 1
                        refused_stop = [
                            a
                            for a in env.acceptance(obs)
                            if any(w in a for w in ("MISSING", "not ", "missing", "absent", "NOT", "yet"))
                        ] or env.acceptance(obs)
                        rec["events"].append(
                            f"none={probs['none']:.2f}, done={done:.2f} but acceptance check fails -> stop refused ({fed_back})"
                        )
                        log["fed_back"] = fed_back
                        emit(
                            "note",
                            step=step,
                            text=f"Jev wants to stop (none={probs['none']:.2f}, done={done:.2f}) but the code-owned check is unmet -> stop refused, unmet items shown to it",
                            unmet=refused_stop,
                        )
                        log["steps"].append(rec)
                        step += 1
                        continue
                    # Reaching here means env.terminal(obs) was FALSE at the top of this step -- a
                    # satisfied checklist exits above with success -- so the code-owned check never
                    # agrees here, whatever the two model signals say. Calling this "fused_done" read
                    # as a legitimate completion; it is the loop giving up with the checklist unmet,
                    # and the run is scored on terminal() either way, so only the label changes.
                    if flags.get("consequences"):
                        rec["events"].append(
                            f"none={probs['none']:.2f}, done={done:.2f}, but the acceptance check is STILL unmet "
                            f"after {fed_back} refusal(s) -> giving up"
                        )
                        log["stopped"] = "gave_up_checklist_unmet"
                        log["unmet_at_stop"] = env.acceptance(obs)
                    else:
                        # No checklist in this variant, so there is nothing to contradict the model. Two
                        # different stops share this branch: Jev's own done signal (none >= NONE_T), and
                        # the loop giving up after 'none' led three plans on a done score below the bar.
                        # The second is not a completion and is labelled as one no longer.
                        if have_values:
                            rec["events"].append(
                                f"all {len(value_clauses)} value(s) the goal asks for are copied and 'none' leads -> stop"
                            )
                            log["stopped"] = "values_in_hand"
                            log["success"] = True
                        elif probs["none"] >= NONE_T or done >= done_t:
                            missing = [c for c in value_clauses if c not in {v.get("wanted") for v in copied.values()}]
                            rec["events"].append(
                                f"stop on the model's own signals: none={probs['none']:.2f}, done={done:.2f}"
                                + (f"; values still missing: {missing}" if missing else "")
                            )
                            log["stopped"] = "stopped_on_done_signal"
                            # No code-owned check exists in this variant, so Jev's own done signal is the completion
                            # -- unless the pre-analysis named values to read and some were never copied (Flights
                            # once ended at none=0.95, done=0.02 with none of its three values read and was reported
                            # as a pass). The pre-analysis over-declares too ("stop when you can read the number"
                            # is the same number), so the register is put to Jev once, not to the clause count.
                            complete = True
                            if missing:
                                complete = (
                                    jev(
                                        {**base, "unread_clauses": missing},
                                        Q_REGISTER_COMPLETE,
                                        "done",
                                    )["answers_question"]["noul"]
                                    >= 0.5
                                )
                                emit(
                                    "note",
                                    step=step,
                                    text=f"stopping on the model's signal with {len(missing)} of {len(value_clauses)} value "
                                    f"clause(s) never read -> register judged {'complete' if complete else 'incomplete: not a success'}",
                                )
                            log["success"] = complete
                        else:
                            # Giving up because a declared value was never read. The declaration is Jev's own
                            # pre-analysis and over-declares ("Include nearby airports" counted as a value on the
                            # flights goal, with time and price both in hand), so before calling this a failure the
                            # register is put to Jev once: complete means the run stops as a success.
                            missing = [c for c in value_clauses if c not in {v.get("wanted") for v in copied.values()}]
                            complete = (
                                bool(copied)
                                and bool(missing)
                                and (
                                    jev({**base, "unread_clauses": missing}, Q_REGISTER_COMPLETE, "done")[
                                        "answers_question"
                                    ]["noul"]
                                    >= 0.5
                                )
                            )
                            if complete:
                                rec["events"].append(
                                    f"'none' led {none_streak} plans; {len(missing)} value clause(s) unread but the register "
                                    "is judged complete -> stop"
                                )
                                emit(
                                    "note",
                                    step=step,
                                    text=f"'none' led {none_streak} plans; {len(missing)} of {len(value_clauses)} value clause(s) "
                                    "never read, but the register is judged complete -> stopping as done",
                                )
                                log["stopped"] = "values_in_hand"
                                log["success"] = True
                            else:
                                rec["events"].append(
                                    f"'none' led {none_streak} plans with done={done:.2f} < {done_t} -> giving up"
                                )
                                log["stopped"] = "gave_up_none_streak"
                    # The goal may have asked to FIND something. Read it off the final page by collapse (1-3
                    # choice calls) so the run ends with the answer, not only a screenshot of it.
                    units = page_units(obs, getattr(env, "_snap", None))
                    copied_vals = [str(v) for k, v in task["facts"].items() if str(k).startswith("copied_")]
                    got = None
                    answer_form, answer_trace = None, []
                    if copied_vals:
                        # A goal that compares or collects several values is answered by the register as a whole,
                        # not by whichever one happens to be on the last page. Mixed into the page units the
                        # register lost to the page's own line (answer "166.4" for a compare-two-heights goal),
                        # so it is asked on its own first: the register, or one value read off this page. One
                        # copied value gets the same question: re-reading the page picked the metro population
                        # (251,912) over the 138,753 already copied for the very clause the goal asked about.
                        reg = "; ".join(copied_vals)
                        qa = {
                            "answer_form": {
                                "type": "choice",
                                "instructions": (
                                    "The agent pursuing `task_goal` is about to report its result. `facts` holds the values it copied during the "
                                    "run (copied_N). Which form answers the goal?"
                                ),
                                "criteria": {
                                    "all_copied": (
                                        f"all the copied values together, as the goal asked for several: `{reg}`"
                                        if len(copied_vals) > 1
                                        else f"the value already copied for the goal's clause: `{reg}`"
                                    ),
                                    "one_on_page": "one single value shown on the current page (the goal asked for one thing)",
                                    "none": "the goal did not ask to find or read information; there is no value to report",
                                },
                            }
                        }
                        form = jev({**base, "state": obs}, qa, "copy")["answer_form"]
                        answer_form = {k: round(v, 3) for k, v in form.get("probabilities", {}).items()}
                        emit(
                            "note",
                            step=step,
                            text=f"answer form: {form['choice']} (p={form['probabilities'].get(form['choice'], 0.0):.2f})",
                        )
                        if form["choice"] == "all_copied":
                            got = {
                                "text": reg,
                                "unit": f"all values copied during the run: {reg}",
                                "rounds": 1,
                                "p": form["probabilities"].get("all_copied", 0.0),
                            }
                        elif form["choice"] == "none":
                            units = []
                    if got is None and units:
                        got = collapse_pick(
                            jev,
                            {**base, "state": obs},
                            units,
                            "as the answer the goal asked for -- only if the goal asked to find or read information",
                            trace=answer_trace,
                            prefilter=prefilter_for("the answer the goal asked for"),
                        )
                    if got:
                        log["answer"] = {"text": got["text"], "context": got["unit"][:200]}
                        emit(
                            "answer",
                            step=step,
                            text=got["text"],
                            context=got["unit"][:160],
                            rounds=got["rounds"],
                            p=round(got.get("p", 0.0), 3),
                            answer_form=answer_form,
                            trace=answer_trace,
                        )
                    log["steps"].append(rec)
                    break
                rec["events"].append(f"none={probs['none']:.2f} but done={done:.2f}: disagree, continue with next-best")
            none_led = ranked[0] == "none"
            ranked = [k for k in ranked if k != "none"]
            act_floor = NONE_LED_FLOOR if none_led else NEXT_BEST_FLOOR
            if ranked and probs.get(ranked[0], 0.0) < act_floor:
                # 'none' had the weight and the loop did not accept it as a stop. Acting on the leftovers
                # is acting on noise; look again instead (the revisit guard bounds how often, and the
                # none-streak counted this plan already).
                best = probs.get(ranked[0], 0.0)
                rec["events"].append(
                    f"no candidate above the floor once 'none' is set aside (best {best:.2f} < {act_floor}) -> re-observe"
                )
                emit(
                    "note",
                    step=step,
                    text=(
                        f"'none' led (p={probs.get('none', 0.0):.2f}) and the best real action carries only {best:.2f} -> not acting on it, looking again"
                        if none_led
                        else "no candidate carries any weight besides 'none' -> re-observe"
                    ),
                )
                log["steps"].append(rec)
                step += 1
                continue
            if ranked:
                below = [k for k in ranked[1:] if probs.get(k, 0.0) < NEXT_BEST_FLOOR]
                ranked = [ranked[0]] + [k for k in ranked[1:] if probs.get(k, 0.0) >= NEXT_BEST_FLOOR]
                if below:
                    rec["events"].append(
                        f"{len(below)} alternatives below the next-best floor ({NEXT_BEST_FLOOR}) are not fallbacks"
                    )
            # The least-bad fallback has to be a candidate still ON OFFER. Checking it against
            # all_cands admitted ids that had been withdrawn (dead, or banned as a toggle), and the
            # lookup below searches `cands` -- which raised StopIteration and lost the whole task.
            if least_bad and least_bad in [c["id"] for c in cands]:
                ranked = [least_bad] + [k for k in ranked if k != least_bad]
                rec["events"].append(
                    f"all alternatives here failed verification; trying reversible least-bad {least_bad} once"
                )
            if not ranked:
                log["steps"].append(rec)
                log["stopped"] = "no_candidates"
                break
            succeeded = replan = dead_end = escalate = stalled = False
            retried_same = False
            attempt = 0
            skipped = 0
            while attempt < MAX_ATTEMPTS and not succeeded and not replan and not escalate and ranked:
                idx = attempt
                pick = ranked[min(idx if not retried_same else idx - 1, len(ranked) - 1)]
                if none_led and probs.get(pick, 0.0) < NONE_LED_FLOOR and not retried_same:
                    # The plan was led by 'none'; the alternatives left after a skip are below the floor.
                    rec["events"].append(
                        f"next-best {pick} carries {probs.get(pick, 0.0):.2f} under a none-led plan -> not acted on, re-plan"
                    )
                    replan = True
                    break
                cand = next((c for c in cands if c["id"] == pick), None)
                if cand is None:
                    # A ranked id that is no longer on offer. Drop it and re-plan rather than raising:
                    # a crash here costs the whole task, and the log then shows nothing about the step.
                    # After an undo the page is different by definition, so a retry whose target is gone
                    # re-plans outright: sliding to the old ranking's next entry put the keyboard on a
                    # 0.02 text box.
                    ranked = [k for k in ranked if k != pick]
                    rec["events"].append(f"ranked action {pick} is no longer offered here -> dropped, re-plan")
                    if not ranked or retried_same:
                        replan = True
                    continue
                if cand["kind"] == "copy":
                    # The register. Nothing on the page changes, so there is no before/after verify; instead the
                    # copy is checked the way a typed value is: WHICH goal clause it is for is decided first
                    # (the purpose of the collapse), and the picked text is judged against that clause before
                    # it is stored. A copy that skipped both took "1985" (completed in) for a height in
                    # meters, p=0.75, and the run finished on it. Offered once per page state.
                    rejected.setdefault(fp, {})["copy"] = 0.0
                    clauses = open_clauses(obs.get("url", ""))
                    wanted = None
                    wanted_probs = None  # the clause question as Jev answered it, for the UI's trace
                    if forced_wanted:
                        wanted, forced_wanted = forced_wanted, None  # decided by the give-up guard just above
                    elif clauses:
                        w = jev({**base, "state": obs}, q_copy_target(clauses), "copy")["wanted"]
                        wanted = None if w["choice"] == "none" else w["choice"]
                        wanted_probs = {k: round(v, 3) for k, v in w.get("probabilities", {}).items()}
                        emit(
                            "note",
                            step=step,
                            text=(
                                f"copying for: `{wanted}` (p={w['probabilities'].get(wanted, 0.0):.2f})"
                                if wanted
                                else "no part of the goal wants a value from this page -> copy skipped"
                            ),
                        )
                    purpose = "to type into a field later or to report as the result" + (
                        f" -- specifically the value that `{wanted}` calls for" if wanted else ""
                    )
                    units = page_units(obs, getattr(env, "_snap", None))
                    got = None
                    # Values already in the register are not offered again: on the flight results the time was
                    # re-picked three times ("already copied") and the copy was withdrawn before the price was read.
                    bad_pieces = [v["text"] for v in copied.values()]
                    attempts_trace = []  # one record per collapse attempt: rounds, result, check
                    if wanted or not clauses:
                        for attempt_copy in range(2):
                            rounds_trace = []
                            got = collapse_pick(
                                jev,
                                {**base, "state": obs},
                                units,
                                purpose,
                                exclude=bad_pieces,
                                trace=rounds_trace,
                                prefilter=prefilter_for(wanted),
                            )
                            if not got:
                                attempts_trace.append(
                                    {"rounds": rounds_trace, "text": None, "ok": None, "accepted": False}
                                )
                                break
                            ok = got.get("p", 0.0)
                            if ok >= verify_t and wanted:
                                ok = jev(
                                    {
                                        **base,
                                        "value_copied": got["text"],
                                        "copied_from": got["unit"][:200],
                                        "wanted": wanted,
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
                            # Wrong kind of value (or a weak pick): that piece is off the last round and the collapse
                            # runs once more -- the line stays, since the right value often shares it with the wrong
                            # one. A second miss means this page does not show the value.
                            emit(
                                "note",
                                step=step,
                                text=f'copied {got["text"]!r} from "{got["unit"][:80]}" does not look like the value '
                                f"`{wanted}` calls for (p={ok:.2f}) -> discarded"
                                + (", one more look" if attempt_copy == 0 else ""),
                            )
                            rec["events"].append(
                                {
                                    "attempt": attempt,
                                    "action": "copy",
                                    "outcome": f"{got['text']!r} rejected p={ok:.2f}",
                                }
                            )
                            bad_pieces.append(got["text"])
                            got = None
                    if attempts_trace or wanted_probs:
                        # `units` is the whole haystack the collapse picked from, kept in full so a run log can be
                        # replayed offline (e.g. to test a pre-filter over the page text against what was picked).
                        emit(
                            "copy_trace",
                            step=step,
                            wanted=wanted,
                            wanted_probs=wanted_probs,
                            attempts=attempts_trace,
                            threshold=verify_t,
                            units=units,
                            picked_unit=got["unit"] if got else None,
                        )
                    if got:
                        if got["text"] in {v["text"] for v in copied.values()}:
                            # The same value again (the page toggles between two states and the copy is re-offered
                            # on each): nothing new to remember. Twice on one page and the copy is withdrawn there.
                            copy_dups[obs.get("url", "")] = copy_dups.get(obs.get("url", ""), 0) + 1
                            emit(
                                "note",
                                step=step,
                                text=f"{got['text']!r} was already copied -> not stored again"
                                + ("; copying is withdrawn on this page" if copy_dups[obs.get("url", "")] >= 2 else ""),
                            )
                            rec["events"].append(
                                {"attempt": attempt, "action": "copy", "outcome": "duplicate value -> skipped"}
                            )
                            ranked = [k for k in ranked if k != pick]
                            if not ranked:
                                replan = True
                            continue
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
                                "action": f'copied "{got["text"]}" from the page (from: {got["unit"][:80]})'
                                + (f" as the value `{wanted}` calls for" if wanted else "")
                                + f"; it is now fact {key}",
                                "side_effect": "none",
                                "verify": round(got.get("ok", 1.0), 2),
                                "progress": 0.5,
                            }
                        )
                        rec["events"].append(
                            {
                                "attempt": attempt,
                                "action": "copy",
                                "outcome": f"copied {got['text']!r} in {got['rounds']} round(s), ok={got.get('ok', 0.0):.2f}",
                            }
                        )
                        succeeded = True
                        # One value read; the same page often shows the next one the goal asks for (the
                        # price beside the time). Ask now which clause is still unsatisfied here, and copy
                        # for it on the next plan -- rather than letting three none-led plans, a dead link
                        # and a field revisit go by before the give-up guard asks the same question.
                        rest = [c for c in open_clauses(obs.get("url", "")) if c != wanted]
                        if rest and wanted:
                            w2 = jev(
                                {
                                    **base,
                                    "facts": task["facts"],
                                    "actions_so_far": render_history(history),
                                    "state": obs,
                                },
                                q_copy_target(rest),
                                "copy",
                            )["wanted"]
                            p2 = w2.get("probabilities", {}).get(w2["choice"], 0.0)
                            if w2["choice"] != "none" and p2 >= verify_t:
                                forced_wanted = w2["choice"]
                                emit(
                                    "note",
                                    step=step,
                                    text=f"`{forced_wanted}` still wants a value this page shows (p={p2:.2f}) -> copying for it next",
                                )
                    else:
                        emit(
                            "note",
                            step=step,
                            text="wanted to copy a value from the page, but nothing on it was chosen -> skip"
                            + (f"; `{wanted}` is not asked about on this page again" if wanted else ""),
                        )
                        rec["events"].append(
                            {"attempt": attempt, "action": "copy", "outcome": "nothing chosen -> skip"}
                        )
                        if wanted:
                            # "Include nearby airports" is a thing to do, not a value to read: asked three
                            # times on the results page, it sent the loop after a details button and a dead
                            # link once the values were already in hand.
                            copy_failed.setdefault(obs.get("url", ""), set()).add(wanted)
                            # ...but another clause may still be readable here: on the Amazon results the
                            # rating clause won the vote twice and failed twice while the price, right beside
                            # it, was never asked for. Let the guard ask again on this page, minus the failed
                            # clause (open_clauses drops it).
                            asked_missing.discard(obs.get("url", ""))
                        ranked = [k for k in ranked if k != pick]
                    if not succeeded and ranked and probs.get(ranked[0], 0.0) < act_floor:
                        # the copy was the only candidate with weight; what is left is below the line 'none'
                        # set, and acting on it is acting on noise (see the floor check above the loop)
                        rec["events"].append(
                            f"leftovers after the copy are below the floor ({act_floor}) -> re-observe"
                        )
                        ranked = []
                    if not succeeded and not ranked:
                        replan = True
                    continue
                side = side_effect(cand, obs)
                value, kind = None, cand["kind"]
                pre_typed = None  # set when keyboard mode already typed into the field
                if cand.get("options"):
                    value = jev(base, q_option(cand), "bind")["option"]["choice"]
                elif cand.get("needs_value") or kind in ("fill", "fill_enter"):
                    used = used_facts.get(cand["target_key"], set())
                    offer = {k: v for k, v in task["facts"].items() if k not in used} or task["facts"]
                    fk = jev(base, q_bind(cand, offer), "bind")["fact"]["choice"]
                    if fk == "none":
                        # Keyboard mode (opt-in variant): the value is not among the facts, so spell it from
                        # the goal one key per judgment. The state is deliberately small -- the goal, this
                        # field, the OTHER form fields with their current values (what tells "Where to?" apart
                        # from "Where from?" when the origin already reads Seattle), what is typed so far and
                        # the site's own suggestions. The whole page has nothing to say about the next key.
                        kb_tried = False
                        if flags.get("keyboard") and hasattr(env, "keyboard") and kind in ("fill", "fill_enter"):
                            kb_tried = True
                            kb_state.pop("picker_opened", None)
                            kb_state.pop("left_as_is", None)
                            kb_state.pop("last_read", None)
                            kb_state.pop("trimmed", None)
                            pre_typed = type_with_keyboard(
                                cand,
                                obs,
                                attempt,
                                env=env,
                                task=task,
                                history=history,
                                emit=emit,
                                step=step,
                                base=base,
                                verify_t=verify_t,
                                kb_bad=kb_bad,
                                kb_typed=kb_typed,
                                kb_value=kb_value,
                                kb_state=kb_state,
                                copied=copied,
                                log=log,
                                user_stopped=user_stopped,
                                render_history_fn=render_history,
                            )
                        left_as_is = kb_state.pop("left_as_is", False)
                        if not pre_typed and kb_tried and kb_state.pop("picker_opened", False):
                            # Not a failure: the field answered the focus by opening a picker, which the
                            # next plan will see and choose from. The field itself stays available.
                            rec["events"].append(
                                {
                                    "attempt": attempt,
                                    "action": cand["desc"][:120],
                                    "outcome": "focus opened a picker -> re-plan with it open",
                                }
                            )
                            replan = True
                            continue
                        if not pre_typed and kb_tried and not left_as_is:
                            # Typing was attempted and abandoned. The page has moved meanwhile (an overlay
                            # opened, text came and went), so the ranking made before it is stale: falling
                            # through to its next-best clicked "stale candidate" twice at every such turn.
                            # Re-plan on a fresh observation instead. The field stays available for one
                            # more try -- a second abandonment withdraws it here.
                            kb_failures[cand["target_key"]] = kb_failures.get(cand["target_key"], 0) + 1
                            rec["events"].append(
                                {
                                    "attempt": attempt,
                                    "action": cand["desc"][:120],
                                    "outcome": "keyboard attempt abandoned -> re-plan",
                                }
                            )
                            if kb_failures[cand["target_key"]] >= 2:
                                rejected.setdefault(fp, {})[cand["id"]] = 0.0
                            replan = True
                            continue
                        if not pre_typed:
                            # Either no fact fits and there is no keyboard, or the keyboard was offered and the
                            # model chose 'done' without a key: the field is already as it should be. Either
                            # way the field is withdrawn here and the next-best of the same ranking is tried.
                            rec["events"].append(
                                {
                                    "attempt": attempt,
                                    "action": cand["desc"][:120],
                                    "outcome": "field left as it is -> skip this action"
                                    if left_as_is
                                    else "no fact fits -> skip this action",
                                }
                            )
                            rejected.setdefault(fp, {})[cand["id"]] = 0.0
                            ranked = [k for k in ranked if k != pick]
                            skipped += 1
                            if not ranked or skipped > 6:
                                break
                            continue
                        kind = "keyboard"
                    else:
                        value = task["facts"].get(fk)
                        cand["_fact_key"] = fk
                        if cand.get("needs_commit"):
                            commit = jev({**base, "state": obs}, q_commit(cand), "commit")["commit"]["choice"]
                            kind = "fill_enter" if commit == "press_enter" else "fill"
                desc = cand["desc"] + (
                    f' with value "{str(value)[:60]}"'
                    if value
                    else (f' -> typed "{pre_typed[:60]}" key by key, suggestions left open' if pre_typed else "")
                )
                if user_stopped():
                    log["stopped"] = "user_stop"
                    escalate = True  # leaves both loops without acting
                    break
                emit(
                    "act",
                    step=step,
                    attempt=attempt,
                    id=cand["id"],
                    desc=desc[:200],
                    action_kind=kind,
                    value=(str(value)[:60] if value is not None else None),
                    side_effect=side,
                    risk=(round(risk_cache[cand["desc"]], 3) if cand["desc"] in risk_cache else None),
                    screenshot=shot(cand.get("idx")),
                )
                injected = None
                if pre_typed:
                    pass  # the keys were already pressed; verification judges the result
                elif fault and attempt == 0:
                    injected = fault["kind"]
                    if fault["kind"] == "noop":
                        env.noop()
                    elif fault["kind"] == "wrong_fill" and value is not None and not cand.get("options"):
                        env.act(cand, wrong_value(task, str(value)), kind)
                    elif fault["kind"] == "detour":
                        if not env.detour(fault):
                            injected = None
                            pending_fault = fault
                            env.act(cand, value, kind)
                    elif fault["kind"] == "delayed_ack" and hasattr(env, "delayed_ack"):
                        env.delayed_ack(
                            cand, value, kind
                        )  # applied server-side, acknowledgement lost, first observation stale
                    elif fault["kind"] == "partial":
                        if not (hasattr(env, "partial") and env.partial(cand, value, kind)):
                            injected = None
                            env.act(cand, value, kind)
                    elif fault["kind"] == "popup":
                        if not (hasattr(env, "popup") and env.popup(cand, value, kind)):
                            injected = None
                            env.act(cand, value, kind)
                    else:
                        injected = None
                        pending_fault = fault
                        env.act(cand, value, kind)
                else:
                    if side == "irreversible":
                        info = {
                            "step": step,
                            "action": desc[:200],
                            "risk": round(risk_cache.get(cand["desc"], 1.0), 3),
                            "policy": irreversible_policy,
                        }
                        if irreversible_policy == "allow":
                            allowed, by = True, "policy"
                        elif irreversible_policy == "ask" and approver is not None:
                            emit("approval", status="pending", **info)
                            try:
                                allowed = bool(approver(info))
                            except Exception:  # noqa: BLE001 - a broken approver is a refusal, never a pass
                                allowed = False
                            by = "operator"
                        else:
                            allowed, by = False, "policy"
                        emit("approval", status="allowed" if allowed else "denied", decided_by=by, **info)
                        if not allowed:
                            rec["events"].append(
                                {
                                    "attempt": attempt,
                                    "action": cand["desc"][:120],
                                    "outcome": f"irreversible action not approved ({by}) -> skipped",
                                }
                            )
                            emit(
                                "note",
                                step=step,
                                attempt=attempt,
                                text=f"irreversible action not approved ({by}) -> not performed, next-best",
                            )
                            rejected.setdefault(fp, {})[cand["id"]] = 0.0
                            ranked = [k for k in ranked if k != pick]
                            skipped += 1
                            if not ranked or skipped > 6:
                                break
                            continue
                        if hasattr(env, "inflight") and env.inflight() > 0:
                            env.wait_inflight(3000)
                    env.act(cand, value, kind)
                after = env.observe()
                if pre_typed and kb_state.get("last_read"):
                    # What the keyboard read back from the focused field the moment it stopped: the text and
                    # the suggestions. Some sites type into an overlay input that the element table may not
                    # attribute to the field the loop named (the form's own box still reads empty), and
                    # verify then sees no change. This is observed, not assumed: the child read the active
                    # element's value.
                    after["typed_into_focused_field"] = kb_state["last_read"]
                after_shot = shot()
                ver, lab, prog_spec = verify_call(base, desc, obs, after, flags)
                ev = {
                    "attempt": attempt,
                    "action": desc[:120],
                    "value": (str(value)[:60] if value is not None else None),
                    "injected_fault": injected,
                    "verify": ver,
                    "side_effect": side,
                }
                if pre_typed:
                    ev["typed_by_keyboard"] = pre_typed[:60]
                if lab:
                    ev["verify_label"] = lab
                if getattr(env, "last_settled", None):
                    ev["settled_ms"] = env.last_settled.get("settled_ms")
                    ev["settled"] = env.last_settled.get("settled")
                    env.last_settled = None
                if getattr(env, "last_action_note", None):
                    ev["action_note"] = env.last_action_note
                    emit("note", step=step, attempt=attempt, text=env.last_action_note)
                    env.last_action_note = None
                if getattr(env, "last_action_error", None):
                    ev["action_error"] = env.last_action_error
                    env.last_action_error = None
                    # A control that will not take a click (covered, detached, disabled by script) is not
                    # going to take the fourth one either: Kayak's "2 guests, 1 room" ate four steps and
                    # eight timeouts. Three errors on one target in a run withdraw it everywhere.
                    act_failures[cand["target_key"]] = act_failures.get(cand["target_key"], 0) + 1
                    if act_failures[cand["target_key"]] >= 3 and cand["id"] not in banned:
                        banned.add(cand["id"])
                        emit(
                            "note",
                            step=step,
                            text=f"{cand['desc'][:80]} failed to take an action {act_failures[cand['target_key']]} times -> withdrawn for the rest of the run",
                        )
                changed = env.fingerprint(after) != fp
                if injected and injected != "popup" and abs(ver - verify_t) <= BORDERLINE_BAND:
                    # This fault was decided by which side of the line a near-tie fell on. Record it:
                    # counted as a plain detection (or miss) it would report a coin flip as a clean
                    # result, and this same instance has landed on both sides across runs.
                    ev["borderline"] = round(ver, 3)
                    log["borderline_faults"] = log.get("borderline_faults", 0) + 1
                loading = ver < verify_t and not injected and hasattr(env, "inflight") and env.inflight() > 0
                if (
                    lab == "unclear"
                    or loading
                    or (side == "irreversible" and ver < verify_t)
                    or (changed and not injected and verify_t - BORDERLINE_BAND <= ver < verify_t)
                ) and not ev.get("reobserved"):
                    # Uncertain outcome of an action we must not repeat, OR a reversible action that changed the
                    # page and landed just under the line: give the world a moment, look again, re-judge. Undoing
                    # a borderline change is not free -- when the URL moved, undo is a browser back, and a date
                    # picker's Done (judged 0.45 with the date set) lost the date that way.
                    if loading:
                        waited = env.wait_inflight(3000)
                        ev["settled_ms"] = waited
                        ev["settled"] = env.last_settled.get("settled") if getattr(env, "last_settled", None) else None
                        env.last_settled = None
                    else:
                        time.sleep(DELAYED_ACK_WAIT_S)
                    after = env.observe()
                    after_shot = shot()
                    ver, lab, prog_spec = verify_call(base, desc, obs, after, flags)
                    ev.update(reobserved=True, verify_after_wait=ver)
                    if lab:
                        ev["verify_label_after_wait"] = lab
                    changed = env.fingerprint(after) != fp
                accept = ver >= verify_t or (least_bad == cand["id"] and not injected and side != "irreversible")
                if accept:
                    ev["outcome"] = "accepted" if ver >= verify_t else "accepted as least-bad"
                    succeeded = True
                    prog = prog_spec  # asked alongside verify on the same before/after state
                    ev["progress"] = prog
                    history.append(
                        {
                            "step": step,
                            "action": desc,
                            "side_effect": side,
                            "verify": round(ver, 2),
                            "progress": round(prog, 2),
                        }
                    )
                    last_taken = (fp, cand["id"], ver < verify_t, side, step)
                    # Stall guard. Verify accepts each of these clicks (the page does change) and the
                    # revisit guard never fires (each state is new), so the loop needs its own view: the
                    # last STALL_K accepted actions all inside one repeated widget, hitting fewer distinct
                    # targets than actions, and Jev's done signal no higher than when the streak began.
                    # Filling four same-styled text boxes in a row passes -- four distinct targets.
                    accepted_fams.append((cand.get("fam"), cand.get("target_key"), done))
                    if len(accepted_fams) >= STALL_K:
                        window = accepted_fams[-STALL_K:]
                        f0 = window[0][0]
                        if (
                            f0
                            and f0 != "scroll"
                            and all(f == f0 for f, _, _ in window)
                            and len({t for _, t, _ in window}) < STALL_K
                            and done <= window[0][2] + 0.05
                        ):
                            log["stopped"] = "stalled_in_widget"
                            stalled = True
                            emit(
                                "note",
                                step=step,
                                text=f"the last {STALL_K} accepted actions all hit the same repeated widget "
                                f"({', '.join(t.split(':', 1)[1] or t for _, t, _ in window)}) and the done signal did not rise -> stalled, stop",
                            )
                    if cand.get("_fact_key"):
                        used_facts.setdefault(cand["target_key"], set()).add(cand["_fact_key"])
                    if pre_typed:
                        # The field has served its purpose with this value. A search box is reused for
                        # different things over a run (Seattle, then Portland): forget the cached choice so
                        # the next visit asks again, and remember what went in for that question.
                        kb_value.pop(cand["target_key"], None)
                        kb_typed.setdefault(cand["target_key"].split(":", 1)[-1], []).append(
                            pre_typed
                        )  # by name: a search box is a searchbox on one page, a combobox on the next
                    if injected and injected not in ("delayed_ack", "popup"):
                        log["faults_missed"] = log.get("faults_missed", 0) + 1
                    if injected == "popup":
                        # An unrelated overlay does not break the action, so accepting the step IS the
                        # right answer. Booked as its own tally: counting it under faults_missed would
                        # report the correct behaviour as a miss.
                        log["popup_ignored"] = log.get("popup_ignored", 0) + 1
                    if injected == "delayed_ack":
                        log["delayed_ack_recovered"] = log.get("delayed_ack_recovered", 0) + 1
                    if prog < PROGRESS_T:
                        no_progress += 1
                        log["no_progress_steps"] = log.get("no_progress_steps", 0) + 1
                        if no_progress >= MAX_NO_PROGRESS and side != "irreversible":
                            # Three accepted steps in a row that only looked around / navigated: stop offering this one here.
                            rejected.setdefault(fp, {})[cand["id"]] = ver
                            ev["outcome"] += f"; {no_progress} consecutive no-progress steps -> rejected here, replan"
                            no_progress = 0
                    else:
                        no_progress = 0
                    if ver < verify_t and not changed:
                        ev["outcome"] = "least-bad action had no effect -> dead end"
                        log["stopped"] = "dead_end"
                        dead_end = True
                else:
                    if injected and injected not in ("delayed_ack", "popup"):
                        log["faults_detected"] = log.get("faults_detected", 0) + 1
                    elif injected == "delayed_ack":
                        log["delayed_ack_unconfirmed"] = log.get("delayed_ack_unconfirmed", 0) + 1
                    elif injected == "popup":
                        # The action DID succeed; the overlay is incidental. Reading it as a failure is
                        # a false alarm, and one worth counting separately -- it is the specific error
                        # this fault exists to look for.
                        log["popup_false_alarm"] = log.get("popup_false_alarm", 0) + 1
                        log["false_alarms"] = log.get("false_alarms", 0) + 1
                    else:
                        log["false_alarms"] = log.get("false_alarms", 0) + 1
                    if side == "irreversible":
                        # Never replay a committing action on failed evidence -- UNLESS the state proves the
                        # request never landed, in which case there is nothing to duplicate and one retry is
                        # safe. The bar is deliberately higher than ordinary verification and the rescue is
                        # allowed once per task, so a wrong "not applied" costs at most one repeated write
                        # and can never loop. Everything else, including any timeout or missing
                        # acknowledgement, still escalates: "no answer" is not proof of non-delivery.
                        not_applied = 0.0
                        if not used_retry_rescue:
                            not_applied = jev(
                                {
                                    "task_goal": base["task_goal"],
                                    "action_taken": desc,
                                    "state_before": obs,
                                    "state_after": after,
                                },
                                Q_NOT_APPLIED,
                                "not_applied",
                            )["answers_question"]["noul"]
                            ev["not_applied"] = not_applied
                        if not_applied >= NOT_APPLIED_T:
                            used_retry_rescue = True
                            log["retry_rescues"] = log.get("retry_rescues", 0) + 1
                            ev["outcome"] = (
                                f"committing action, but the state proves it never landed "
                                f"({not_applied:.2f}) -> retry once"
                            )
                            # Same single-retry semantics as the reversible path: nothing changed, so re-judge
                            # from this state, and if the repeat fails too the action is dropped here.
                            retried_same = True
                            obs = after
                        else:
                            ev["outcome"] = "irreversible action with unconfirmed outcome -> escalate"
                            log["stopped"] = "escalate_unconfirmed_irreversible"
                            log["escalated"] = True
                            escalate = True
                    else:
                        if changed:
                            if pre_typed and hasattr(env, "keyboard") and not obs.get("open_dialog"):
                                # The typing opened a suggestion overlay; undo restores the field but does not
                                # close it, and the next plan then clicked page links behind a stale panel.
                                env.keyboard(key="Escape")
                            if pre_typed:
                                # The value as typed did not do what the field needed. Forget it for this field
                                # and keep it out of the next value choice, so the field is not fed the same
                                # text on every visit ("Paris CDG" four times over).
                                kb_value.pop(cand["target_key"], None)
                                kb_bad.setdefault(cand["target_key"], set()).add(pre_typed)
                            env.undo()
                            log["backtracks"] = log.get("backtracks", 0) + 1
                            ev["_undone"] = True
                            # A page with (almost) nothing on it after a navigation to another site is a wall
                            # (a bot check, a block page). Remember the host: the next plan will otherwise
                            # offer the same site's other links, and each costs two attempts and two undos.
                            host_after = urllib.parse.urlsplit(after.get("url") or "").hostname or ""
                            host_before = urllib.parse.urlsplit(obs.get("url") or "").hostname or ""
                            if host_after and host_after != host_before and len(after.get("elements", [])) <= 2:
                                h = host_after.removeprefix("www.")
                                if h not in walled_hosts:
                                    walled_hosts.add(h)
                                    text = " ".join((after.get("visible_text") or "").split())[:120]
                                    emit(
                                        "note",
                                        step=step,
                                        text=f'{h} answered with a page that has nothing to act on ("{text}") -> treating it as a wall; links to it are no longer offered this run',
                                    )
                        if not retried_same and not ev.get("action_error"):
                            ev["outcome"] = ("undone" if changed else "no effect") + " -> retry same action"
                            retried_same = True
                            if changed:
                                # Re-observe AND re-resolve the candidates: every snapshot re-stamps the
                                # element indices, so the old list's `idx` now names whatever happens to
                                # sit at that position. The retry once clicked a Google Travel search link
                                # that way. The id (role|name|context) is what survives; if the same action
                                # is no longer offered, the lookup below drops it and re-plans.
                                obs = env.observe()
                                fresh = {c["id"]: c for c in env.candidates()}
                                cands = [fresh[c["id"]] for c in cands if c["id"] in fresh]
                            else:
                                obs = after
                        else:
                            rejected.setdefault(fp, {})[cand["id"]] = max(
                                ver, rejected.get(fp, {}).get(cand["id"], 0.0)
                            )
                            if changed:
                                ev["outcome"] = "undone again -> rejected here, replan"
                                replan = True
                            else:
                                ev["outcome"] = (
                                    "the page refused the action -> rejected here, try next-best"
                                    if ev.get("action_error") and not retried_same
                                    else "no effect again -> rejected here, try next-best"
                                )
                                obs = after
                undone = ev.pop("_undone", False)
                emit(
                    "verify",
                    step=step,
                    attempt=attempt,
                    action=desc[:200],
                    verify=round(ver, 3),
                    threshold=verify_t,
                    accepted=bool(accept),
                    changed=bool(changed),
                    outcome=ev.get("outcome"),
                    progress=ev.get("progress"),
                    not_applied=ev.get("not_applied"),
                    reobserved=ev.get("reobserved", False),
                    action_error=ev.get("action_error"),
                    settled_ms=ev.get("settled_ms"),
                    settled=ev.get("settled"),
                    side_effect=side,
                    screenshot=after_shot,
                    undone=undone,
                    screenshot_after_undo=(shot() if undone else None),
                )
                rec["events"].append(ev)
                attempt += 1
            if not succeeded and not replan and not escalate:
                rec["events"].append("step exhausted attempts")
                log["exhausted_steps"] = log.get("exhausted_steps", 0) + 1
            rec["elapsed_s"] = round(time.monotonic() - t0, 1)
            log["steps"].append(rec)
            step += 1
            if dead_end or escalate or stalled:
                break
        else:
            # `while` ran out: the only exit without a recorded reason.
            if not log["success"] and log.get("stopped") is None:
                log["stopped"] = "max_steps"
    finally:
        env.close()
    if pending_fault:
        # It never found a step whose action could express it. Say so: a fault that quietly failed to
        # fire would otherwise read as one the run handled.
        log["fault_never_injected"] = pending_fault["kind"]
    log.update(jev_calls=len(CALLS) - calls0, seconds=round(time.monotonic() - t0, 1), history=history)
    emit(
        "end",
        success=log["success"],
        stopped=log.get("stopped"),
        steps=len(log["steps"]),
        jev_calls=log["jev_calls"],
        seconds=log["seconds"],
        backtracks=log.get("backtracks", 0),
        escalated=bool(log.get("escalated")),
        unmet=log.get("unmet_at_stop"),
        history=render_history(history),
        answer=log.get("answer"),
        copied={k: v["text"] for k, v in copied.items()},
        in_tok=sum(c["in_tok"] for c in CALLS[calls0:]),
    )
    return log
