# How JevOnly works

JevOnly separates **option construction** from **judgment**. Python and JavaScript inspect the browser, derive a finite set of legal choices, and execute one choice. Jev receives state plus closed questions and returns either a probability (`noul`) or a probability distribution over supplied options. It does not return free-form actions or values.

## One loop iteration

| Phase          | Code-owned work                                                                                                                                                                                                                                      | Jev judgment                                      | Result                                                                                                                     |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Observe        | Snapshot URL, title, headings, visible text, enabled controls, values, state, and nearby context.                                                                                                                                                    | None.                                             | A bounded accessibility-oriented state.                                                                                    |
| Build          | Convert controls to click/fill/select actions; add copy and, in viewport mode, scroll/find.                                                                                                                                                          | None.                                             | Candidate ids remain stable across ordinary re-renders because they hash role, name, context, and duplicate position.      |
| Judge and plan | Reuse one state for multiple question heads.                                                                                                                                                                                                         | Completion, off-path, and next action.            | A probability for completion/off-path plus a ranked action list.                                                           |
| Bind           | Offer facts, a control's options, deterministic goal spans, or page-derived values. For up to six value-taking controls the question rides on the plan request ("if this is the pick, which value"), so choosing a field costs no second round trip. | Pick one supplied value or `none`.                | The model never writes field contents.                                                                                     |
| Risk           | Classify unknown side effects once per action description.                                                                                                                                                                                           | Probability that the action commits an effect.    | Irreversible actions receive fail-closed retry behavior.                                                                   |
| Act            | Send one JSON-lines command to the Node child, carrying the control's semantic signature (role, label, value, state, options) from the observation. The child recomputes it just before dispatch and refuses, unsent, when it differs.               | None.                                             | Chromium clicks, fills, selects, scrolls, finds, or types. A refused dispatch is a fresh observation, not a failed action. |
| Verify         | Observe again and compare fingerprints.                                                                                                                                                                                                              | Intended effect and progress.                     | Accept, retry, undo, reject, or escalate.                                                                                  |
| Finish         | Apply a code-owned terminal check when configured; otherwise combine `done` and `none` signals.                                                                                                                                                      | Completion and, for read goals, answer selection. | A terminal `end` event and structured result.                                                                              |

## The exact core questions

The strings below are the `instructions` sent by the current question builders.

### Choose the next action

> An agent is working toward `task_goal` using the information in `facts`, has already done `actions_so_far`, and observes `state`. Which candidate action should it take NEXT? Read each candidate's description carefully; several may look alike. Complete every required part of a form or structure before submitting or finishing.

Choices are every currently offered candidate plus:

> No listed action should be taken next: the task is already complete, or this state cannot advance it.

### Is the task done?

> An agent pursuing `task_goal` has done `actions_so_far` and now observes `state`. Check every part of the goal against the history and the state: is the task fully complete, so the agent should stop?

### Is the state off path?

> An agent pursuing `task_goal` has done `actions_so_far` and now observes `state`. Is this state OFF the task's path, i.e. a place from which continuing forward cannot advance the goal, so the agent should go back / undo?

### Did the action work?

> An agent performed `action_taken` as part of `task_goal`. Comparing `state_before` and `state_after`, did the action have its intended effect?

Verification deliberately asks only whether the action achieved its stated effect. Progress is a separate head:

> An agent performed `action_taken` as part of `task_goal`, having already done `actions_so_far`. Comparing `state_before` and `state_after`: did this step move the task CLOSER to completion, as opposed to merely looking around, navigating, or re-doing something already done?

### Would the action commit something irreversible?

> In the context of `state`, would performing `candidate_action` COMMIT something that cannot be undone by simply navigating back or editing a field again?

### Was an irreversible request definitely not applied?

> An agent performed `action_taken`, a committing request that must never be sent twice if the first one landed. Comparing `state_before` and `state_after`, does the state PROVE the request was never applied at all?

A timeout or missing acknowledgment is explicitly not proof. The loop escalates unless the returned state proves no part of the request landed.

## Where choices come from

### Actions

The Node snapshot contains visible, enabled interactive controls. Python describes each using role, accessible name, context, current value, selectable options, checked/expanded state, and modal scope. It also adds:

| Synthetic choice | When offered                                                   | Effect                                                                                                                                                                                                        |
| ---------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `copy`           | The page exposes text.                                         | Reads a page value into the fact register without changing the page.                                                                                                                                          |
| `scroll up/down` | Viewport mode detects more content.                            | Moves by about 80% of the viewport.                                                                                                                                                                           |
| `find`           | A goal span, useful goal word, or fact could occur off screen. | Finds and centers page text, like Ctrl+F.                                                                                                                                                                     |
| `none`           | Always in an action vote.                                      | Signals completion or inability to advance.                                                                                                                                                                   |
| `back`           | The previous page differs from this one.                       | The browser's Back button, as a choice: "return to the search results" is a move the model can pick, not only an undo.                                                                                        |
| `fill_form`      | At least two empty fields (text or select) and some facts.     | Fills the whole form in one pass: one request binds every field to a fact or option (or `none`), one action fills them, one verification judges the result, one undo restores them all. Nothing is submitted. |
| `press_enter`    | A search box (or a typed combobox) that holds text.            | Presses Enter in that box to submit what it holds. The typing path never presses Enter on its own; this is the choice that finishes a search with no suggestion to pick. Undo goes back.                      |

### Goal values

`goal_spans` deterministically extracts at most 20 values:

1. quoted strings;
2. ALL-CAPS codes;
3. capitalized phrases with attached numbers;
4. numeric dates and numbers of at least three digits.

The model selects from those exact substrings. A field can also use a caller-supplied fact or a value already copied from the page.

### Copy values

Before copying, the goal is split into at most 16 clauses at punctuation and at `and`/`then`. Jev first chooses which clause still needs a page value. Page text is then narrowed in repeated closed votes:

1. Build units from page text, control values, and row/list/table context.
2. Group units into at most 10 consecutive, roughly equal parts.
3. Ask which part **contains** the value; repeat on the chosen part.
4. For one final line, offer regex-derived prices, times, dates, codes, numbers with units, capitalized phrases, words, and the whole line.
5. Ask which piece **is** the value.
6. Check that the selected piece has the right kind, subject, unit, and completeness for the chosen goal clause.

A failed value check removes that piece and permits one more collapse attempt. Accepted values become `copied_N` facts.

### Keyboard values

The model does not generate a string. It first chooses a deterministic goal span or `on_page`. When a value is known, code computes the remaining text and Jev chooses only:

| Choice | Meaning                                                                      |
| ------ | ---------------------------------------------------------------------------- |
| `word` | Type the next word, then inspect autocomplete again.                         |
| `all`  | Type all remaining text.                                                     |
| `done` | Stop because the value is complete or an exact site suggestion is available. |

If no goal span fits, the fallback keyboard vote is bounded to letters, digits, space, `- . , / @`, Backspace, left, right, and `done`. Typing stops after 32 key decisions or when the same rendered field state appears three times.

## Decision thresholds

These values are constants in `jevonly.core.loop`, `jevonly.core.keyboard`, and `jevonly.core.text`.

| Signal or guard                   |   Value | Code behavior                                                                                                                                         |
| --------------------------------- | ------: | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Verification                      |  `0.50` | Accept an action at or above the threshold.                                                                                                           |
| Off path                          |  `0.60` | Undo the last reversible, non-weak action and re-plan.                                                                                                |
| Done                              |  `0.70` | Treat Jev as saying the task is done; a code check can still refuse.                                                                                  |
| Strong `none`                     |  `0.80` | With `done >= 0.35`, stop or enter completion-check feedback.                                                                                         |
| Weak done                         |  `0.35` | Minimum done score for a single strong `none` vote to stop the run.                                                                                   |
| Irreversible action (`risk`)      |  `0.50` | An action at or above this is gated: `refuse` skips it (CLI default), `ask` waits for the operator (viewer default), `allow` performs it.             |
| Consecutive `none` plans          |     `3` | Three `none`-led plans in a row, at any done score, is the stop; a `none`-led plan with value clauses still unread first forces one copy (see below). |
| Candidate under a `none`-led plan |  `0.50` | Once `none` leads, the best real action must hold this share of the non-`none` vote to be tried; otherwise the loop looks again.                      |
| Copy accepted                     |  `0.50` | The clause check on a copied value must read at least this; a reading within `0.10` under it is asked once more with the surrounding lines.           |
| Off-path repeat margin            |  `0.15` | After an undo, the same move is undone again only when off-path clears the line by this much; under it the planner's repeated choice stands.          |
| Next-best fallback                |  `0.02` | Alternatives below this probability are not fallbacks.                                                                                                |
| Progress                          |  `0.25` | Below this, count an accepted step as no progress.                                                                                                    |
| Consecutive no-progress steps     |     `3` | Reject the latest reversible action at that state and re-plan.                                                                                        |
| Irreversible risk                 |  `0.50` | Apply irreversible-action handling.                                                                                                                   |
| Proven not applied                |  `0.50` | Permit the one run-wide retry of a committing request.                                                                                                |
| Borderline verification band      | `±0.06` | Record borderline injected faults; changed near-misses are re-observed.                                                                               |
| Re-observation wait               | `1.5 s` | Pause before rechecking an unclear or consequential near-miss.                                                                                        |
| Attempts per plan                 |     `3` | Bound same/next-best retries.                                                                                                                         |
| Completion-check feedback         |     `2` | Show unmet checks to Jev at most twice before giving up.                                                                                              |
| Repeated-state visits             |   `> 8` | Stop as a cycle.                                                                                                                                      |
| Repeated-widget window            |     `4` | Stop when four accepted actions repeat one widget family without improving done.                                                                      |
| Copy fan-out                      |    `10` | Maximum groups per narrowing round.                                                                                                                   |
| Copy rounds                       |     `6` | Maximum narrowing rounds per collapse attempt.                                                                                                        |
| Keyboard decisions                |    `32` | Clear and abandon a field when exceeded.                                                                                                              |

The `start` event publishes the run-specific thresholds used by the loop so event consumers do not need to duplicate most constants.

## Undo, retry, and escalation

| Situation                                                              | Response                                                                                                                                                    |
| ---------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Reversible action changes the wrong state                              | Undo, refresh candidates, retry once, then reject it at that state.                                                                                         |
| Reversible action has no effect                                        | Retry once, then try the next-best candidate.                                                                                                               |
| Control changed between observation and dispatch                       | Refuse the action unsent (`stale candidate`), re-observe and re-plan. Nothing is verified, undone, or held against the control.                             |
| State is off path                                                      | Undo the last reversible action and withdraw it at the prior state.                                                                                         |
| Navigation lands on an empty wall                                      | Wait twice for 1.5 seconds, go back, and stop offering links to that host for the run. An open dialog with two controls (a consent prompt) is not a wall.   |
| Action repeatedly returns to a known unchanged state                   | Withdraw it as a toggle after two such returns. "Unchanged" is the acceptance checklist when the task has one, and the size of the copy register otherwise. |
| Irreversible action is unconfirmed                                     | Retry only if `not_applied >= 0.50` and the one retry allowance is unused; otherwise escalate.                                                              |
| Every remaining action failed and the least-bad choice is irreversible | Escalate rather than force it.                                                                                                                              |
| Undo could not restore the state                                       | Escalate (`escalate_undo_failed`). The desktop environment reports this for a press it cannot compensate; the browser always restores.                      |

## Completion modes

| Mode                      | Meaning                                                                                                                                                                                                                   |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Code-owned terminal check | `BrowserEnv.terminal` must pass. Jev's done signal is advisory and unmet acceptance details can be fed back twice.                                                                                                        |
| No terminal check         | A strong `none` or done signal can stop the run. Once every value the goal asks for is copied and `none` leads, the run ends as `values_in_hand`. Three weak `none`-led plans produce `gave_up_none_streak`, not success. |
| Read/report goal          | At stop, JevOnly can collapse page text into one answer or report the full copy register when the goal asks for several values.                                                                                           |

## Moving the lines

`verify`, `offpath`, `done` and `risk` are per task: `task["thresholds"] = {"verify": 0.6, ...}`, the
viewer's sliders, or `jevonly run --threshold verify=0.6 --threshold risk=0.3`. The other constants are
module defaults. `task["irreversible"]` selects the gate policy; with `ask`, `task["approve"](info)` is
called with `{step, action, risk, policy}` and must return a boolean -- the viewer answers it from the
approval bar, the CLI from a terminal prompt, and anything that cannot answer (no terminal, an exception,
five minutes of silence in the viewer) counts as no.

A `none`-led plan while value clauses are still unread means "no navigation is needed here", not "nothing is left to do": on a results page the values are on screen and `copy` polls a fraction of the vote. So the first `none`-led plan on a page asks Jev which unread clause this page could answer and, when one is named, forces a copy for it before anything else -- whatever the strength of `none`. The question is asked on up to two `none`-led plans per page (the same page answered the price clause at 0.38 in one run and `none` in the next; the second ask absorbs that variance, and the third `none`-led plan is the stop anyway); a `none` answer is noted, never silent. A clause for which nothing was chosen there is not offered on that page again, but the guard may ask again for the remaining clauses (the rating failed twice on the Amazon results while the price beside it was never asked for).

After a value is copied, the loop asks once more which clause this page can still answer and, when one is named with confidence, copies for it on the very next plan -- the price beside the time is read without waiting for three `none`-led plans. That chained copy outranks a weak stop: on the closed-PR list the number was copied, the title named next, and the following `none`-led plan would otherwise have ended the run with the title never read.

## One page, one register

Every per-page memo -- the guard's once-per-page question, clauses that yielded nothing on a page, duplicate copies, off-path undos -- is keyed by `page_key(url)`: the URL without its query string (results pages rewrite it on every visit) and without its fragment, except a hash route (`#/orders/12`), which is the page. The exact state, text and all, is the fingerprint, and it keys what is about the state: actions rejected there, visits.

The copy register is keyed by (clause, page). A clause is not offered again on the page that already answered it, and it is offered on every other page: "note its height in meters" is read once on Seattle's article and once on Portland's, "note its star rating" once per product. The same text is a duplicate only for the same clause on the same page -- two products can both rate 4.5. A copy forced by a guard belongs to the page it was named on and is dropped when the next observation is another page.

## How many values does the goal want?

Before the first step, one request asks Jev, per goal clause, whether that clause asks for a value to be
read (a time, a price, a name) or for something to be done, and whether meeting it needs a value no other
clause collects ("stop when you can compare the two heights" needs the second height). Only value clauses are ever offered as copy
targets -- "include nearby airports" is never mistaken for a value to look for -- and once each value
clause has a copied value and `none` leads a plan, the run stops as `values_in_hand` with those values as
the answer. A clause for which nothing on a page was chosen is not asked about on that page again.

## Narrowing the goal to a value

When a field wants a value and no short span of the goal (a name, a code, a date) fits, the loop does not
spell from the goal. It hands Jev the goal's clauses and lets it narrow: pick the clause, then, round after
round, either stop (`all`: this exact text is the value) or slide one end of the window inward by 1, 2, 4,
8 ... words -- a two-ended sliding window, so a value in the middle of a clause is reached from both sides
and nothing ever cuts through it. The code decides no round count -- the text only ever gets shorter, so the search ends
when Jev says it does (a safety cap of 16 rounds exists, never reached in practice). `tallest building in
Portland, Oregon` is reached in three rounds from a four-clause goal. Every round is a `copy_trace` row
(`source: "goal"`), with the moves offered and their probabilities.
