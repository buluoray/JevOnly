# Event reference

`jevonly.core.loop.run_task(..., on_event=callback)` calls:

```python
callback(kind, payload)
```

`kind` is one of the 13 values in `EVENT_KINDS`. `payload` does not include `kind` or a timestamp. Fields documented as optional can be absent, `None`, or populated depending on the path.

## Event index

| Kind         | Emitted when                                                                         |
| ------------ | ------------------------------------------------------------------------------------ |
| `start`      | The browser opens and run settings are known.                                        |
| `observe`    | A step snapshots the current page.                                                   |
| `judge`      | Done and off-path scores have been read.                                             |
| `plan`       | Candidate action probabilities have been ranked.                                     |
| `approval`   | An action Jev judged irreversible reaches the gate: pending, then allowed or denied. |
| `act`        | Immediately before a browser action.                                                 |
| `verify`     | An action's before/after judgment is complete.                                       |
| `undo`       | The loop explicitly goes back outside the ordinary action-verification row.          |
| `key`        | Keyboard mode makes or records one bounded typing choice.                            |
| `note`       | The loop explains a branch, guard, withdrawal, or recovery.                          |
| `copy_trace` | Page-value narrowing attempts are summarized.                                        |
| `copy`       | A checked page value enters the fact register.                                       |
| `answer`     | A final page or register value is reported.                                          |
| `end`        | The browser loop has closed and the result is final.                                 |

## Common conventions

| Field         | Convention                                                                                  |
| ------------- | ------------------------------------------------------------------------------------------- |
| `step`        | Zero-based loop step. User interfaces normally display `step + 1`.                          |
| `attempt`     | Zero-based action attempt inside a step.                                                    |
| Scores        | Numbers from `0.0` to `1.0`; most event scores are rounded to three decimals.               |
| `screenshot*` | Base64 JPEG string when an event callback is active and capture succeeds; otherwise `None`. |
| Candidate id  | Stable hash-derived id such as `e12ab34`, or a pseudo-id such as `copy`/`find`.             |

## Payloads

### `start`

| Field            | Type           | Meaning                                                                                  |
| ---------------- | -------------- | ---------------------------------------------------------------------------------------- |
| `task_id`        | string         | `task["id"]`.                                                                            |
| `goal`           | string         | Natural-language goal.                                                                   |
| `start`          | string or null | Starting URL.                                                                            |
| `variant`        | string         | Active loop variant.                                                                     |
| `browser`        | object or null | Probe fields `webdriver`, `headless`, `profile`, `channel`, and `ua`.                    |
| `has_code_check` | boolean        | Whether `task["terminal"]` is non-empty.                                                 |
| `max_steps`      | integer        | Effective step cap.                                                                      |
| `viewport_only`  | boolean        | Whether observations exclude off-screen content.                                         |
| `thresholds`     | object         | `verify`, `offpath`, `done`, `none`, `done_weak`, `risk`, `not_applied`, and `progress`. |

### `observe`

| Field        | Type           | Meaning                                                       |
| ------------ | -------------- | ------------------------------------------------------------- |
| `step`       | integer        | Current step.                                                 |
| `url`        | string or null | Observed URL.                                                 |
| `title`      | string or null | Page title.                                                   |
| `n_elements` | integer        | Number of listed elements.                                    |
| `headings`   | array or null  | Observed page headings.                                       |
| `popups`     | array or null  | Dialog messages from the last action.                         |
| `screenshot` | string or null | Viewport JPEG.                                                |
| `acceptance` | array or null  | Code-owned acceptance checklist when a terminal check exists. |

### `judge`

| Field        | Type    | Meaning                                               |
| ------------ | ------- | ----------------------------------------------------- |
| `step`       | integer | Current step.                                         |
| `done`       | number  | Done score; `0.0` before any accepted history exists. |
| `offpath`    | number  | Off-path score.                                       |
| `done_asked` | boolean | Whether Jev was actually asked the done question.     |

### `plan`

| Field          | Type    | Meaning                                                                                          |
| -------------- | ------- | ------------------------------------------------------------------------------------------------ |
| `step`         | integer | Current step.                                                                                    |
| `n_candidates` | integer | Candidates offered in this vote.                                                                 |
| `n_dead`       | integer | Candidates already rejected at this page fingerprint.                                            |
| `none`         | number  | Probability assigned to `none`.                                                                  |
| `ranked`       | array   | Up to six `{id, p, desc}` objects in descending probability. `desc` is capped at 200 characters. |

### `approval`

Emitted only for actions whose risk score crossed the `risk` line. With policy `ask` two events are
emitted: `pending` while the approver is consulted, then the decision. With `refuse` or `allow` only the
decision is emitted, `decided_by: "policy"`.

| Field        | Type    | Meaning                                                          |
| ------------ | ------- | ---------------------------------------------------------------- |
| `step`       | integer | Current step.                                                    |
| `status`     | string  | `pending`, `allowed` or `denied`.                                |
| `action`     | string  | The action description, capped at 200 characters.                |
| `risk`       | number  | The risk score that classified it.                               |
| `policy`     | string  | `refuse`, `ask` or `allow` -- the task's `irreversible` setting. |
| `decided_by` | string  | `policy` or `operator` (decision events only).                   |

### `act`

| Field         | Type           | Meaning                                                                           |
| ------------- | -------------- | --------------------------------------------------------------------------------- |
| `step`        | integer        | Current step.                                                                     |
| `attempt`     | integer        | Attempt within the step.                                                          |
| `id`          | string         | Candidate id.                                                                     |
| `desc`        | string         | Resolved action description, capped at 200 characters.                            |
| `action_kind` | string         | Such as `click`, `fill`, `fill_enter`, `select`, `scroll`, `find`, or `keyboard`. |
| `value`       | string or null | Bound value, capped at 60 characters. Keyboard text appears in `desc` instead.    |
| `side_effect` | string         | `none`, `reversible`, `navigation`, or `irreversible`.                            |
| `risk`        | number or null | Cached irreversible-risk score when a risk vote was needed.                       |
| `screenshot`  | string or null | Viewport JPEG with the target highlighted when possible.                          |

### `verify`

| Field                   | Type            | Meaning                                                                                    |
| ----------------------- | --------------- | ------------------------------------------------------------------------------------------ |
| `step`                  | integer         | Current step.                                                                              |
| `attempt`               | integer         | Attempt within the step.                                                                   |
| `action`                | string          | Action description, capped at 200 characters.                                              |
| `verify`                | number          | Final intended-effect score.                                                               |
| `threshold`             | number          | Acceptance threshold used for this run.                                                    |
| `accepted`              | boolean         | Whether the loop accepted the action, including a permitted reversible least-bad action.   |
| `changed`               | boolean         | Whether the final observed fingerprint differs from the pre-action fingerprint.            |
| `outcome`               | string or null  | Human-readable branch result.                                                              |
| `progress`              | number or null  | Progress score, used only when accepted.                                                   |
| `not_applied`           | number or null  | Proof-of-non-delivery score for a failed irreversible action.                              |
| `reobserved`            | boolean         | Whether the loop waited and judged a fresh observation.                                    |
| `action_error`          | string or null  | Browser-child action error.                                                                |
| `settled_ms`            | number or null  | Milliseconds the loop waited for in-flight requests before re-judging (0 when it did not). |
| `settled`               | boolean or null | Whether relevant requests settled before the cap.                                          |
| `side_effect`           | string          | Classified side effect.                                                                    |
| `screenshot`            | string or null  | State after the action or re-observation.                                                  |
| `undone`                | boolean         | Whether this verification path undid the changed state.                                    |
| `screenshot_after_undo` | string or null  | Restored state when `undone` is true.                                                      |

### `undo`

| Field        | Type           | Meaning                 |
| ------------ | -------------- | ----------------------- |
| `step`       | integer        | Current step.           |
| `reason`     | string         | Why the loop went back. |
| `screenshot` | string or null | State after undo.       |

This standalone event is used for off-path and empty-wall recovery. Verification-triggered undo is represented by `verify.undone` and `verify.screenshot_after_undo`.

### `key`

| Field         | Type    | Meaning                                                                              |
| ------------- | ------- | ------------------------------------------------------------------------------------ |
| `step`        | integer | Current step.                                                                        |
| `attempt`     | integer | Attempt within the step.                                                             |
| `field`       | string  | Field description, capped at 120 characters.                                         |
| `choice`      | string  | A typed chunk/key, `done`, `replace`, `backspace`, `left`, or `right`.               |
| `p`           | number  | Probability of the selected model choice; deterministic spelling branches use `1.0`. |
| `typed`       | string  | Field value with `                                                                   | `caret or`[selected text]` markers when available. |
| `suggestions` | array   | Up to six visible autocomplete labels.                                               |

### `note`

| Field     | Type              | Meaning                                                       |
| --------- | ----------------- | ------------------------------------------------------------- |
| `step`    | integer           | Current step.                                                 |
| `text`    | string            | Explanation of the code-owned branch.                         |
| `attempt` | integer, optional | Present when the note belongs to one action/keyboard attempt. |
| `unmet`   | array, optional   | Code-owned acceptance items that prevented stopping.          |

### `copy_trace`

| Field          | Type           | Meaning                                                         |
| -------------- | -------------- | --------------------------------------------------------------- |
| `step`         | integer        | Current step.                                                   |
| `wanted`       | string or null | Goal clause or field purpose selected for the copy.             |
| `wanted_probs` | object or null | Clause-to-probability mapping when a clause vote occurred here. |
| `attempts`     | array          | Collapse attempts described below.                              |
| `threshold`    | number         | Minimum accepted copy/check score.                              |

Each attempt contains:

| Field      | Type             | Meaning                                 |
| ---------- | ---------------- | --------------------------------------- |
| `rounds`   | array            | Narrowing rounds.                       |
| `text`     | string or null   | Final selected piece.                   |
| `unit`     | string, optional | Source line, capped at 160 characters.  |
| `p`        | number, optional | Final selection probability.            |
| `ok`       | number or null   | Value-check score.                      |
| `accepted` | boolean          | Whether this attempt met the threshold. |

Each round contains `kind` (`parts` or `pieces`), `pick`, and `options`. Each option is `{id, text, p}` with text capped at 160 characters.

### `copy`

| Field     | Type           | Meaning                                |
| --------- | -------------- | -------------------------------------- |
| `step`    | integer        | Current step.                          |
| `text`    | string         | Copied value.                          |
| `context` | string         | Source unit, capped at 160 characters. |
| `key`     | string         | Register key such as `copied_1`.       |
| `rounds`  | integer        | Number of narrowing rounds.            |
| `p`       | number         | Final piece-selection probability.     |
| `ok`      | number         | Final value-check score.               |
| `wanted`  | string or null | Goal clause or field purpose.          |

### `answer`

| Field         | Type           | Meaning                                                                            |
| ------------- | -------------- | ---------------------------------------------------------------------------------- |
| `step`        | integer        | Final step.                                                                        |
| `text`        | string         | Reported value or semicolon-separated register.                                    |
| `context`     | string         | Source unit or register description, capped at 160 characters.                     |
| `rounds`      | integer        | Selection rounds; one for a full-register answer.                                  |
| `p`           | number         | Selection probability.                                                             |
| `answer_form` | object or null | Probabilities for `all_copied`, `one_on_page`, and `none` when that vote occurred. |
| `trace`       | array          | Copy-style narrowing rounds for a single page answer.                              |

### `end`

| Field        | Type           | Meaning                                                   |
| ------------ | -------------- | --------------------------------------------------------- |
| `success`    | boolean        | Whether the code-owned terminal check passed.             |
| `stopped`    | string or null | Stop reason when not successful.                          |
| `steps`      | integer        | Number of step records.                                   |
| `jev_calls`  | integer        | Jev calls made during this run.                           |
| `seconds`    | number         | Runtime rounded to 0.1 seconds.                           |
| `backtracks` | integer        | Undo/backtrack count.                                     |
| `escalated`  | boolean        | Whether safety logic escalated.                           |
| `unmet`      | array or null  | Acceptance items still unmet at stop.                     |
| `history`    | array          | Accepted action descriptions; undone actions are removed. |
| `answer`     | object or null | Structured `{text, context}` answer.                      |
| `copied`     | object         | Register key-to-text mapping.                             |
| `in_tok`     | integer        | Input tokens reported across Jev calls.                   |

Known `stopped` values are:

| Value                                 | Meaning                                                                                                             |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `user_stop`                           | Cooperative stop callback returned true.                                                                            |
| `budget_exhausted`                    | Time or backtrack budget exceeded.                                                                                  |
| `cycle`                               | A page fingerprint appeared more than eight times.                                                                  |
| `site_unavailable`                    | The start page exposed no actions.                                                                                  |
| `no_candidates`                       | Nothing remained to try.                                                                                            |
| `gave_up_checklist_unmet`             | Jev wanted to stop but a code-owned check remained false after feedback.                                            |
| `stopped_on_done_signal`              | No code-owned check contradicted the model's strong stop signal. Counts as success when no code-owned check exists. |
| `gave_up_none_streak`                 | `none` led three plans while done remained below the normal threshold.                                              |
| `values_in_hand`                      | Every clause the pre-analysis marked as asking for a value has a copied value, and `none` leads. Counts as success. |
| `stalled_in_widget`                   | Repeated accepted actions in one widget family did not raise done.                                                  |
| `dead_end`                            | The reversible least-bad action had no effect.                                                                      |
| `escalate_no_safe_action`             | Only an irreversible failed alternative remained.                                                                   |
| `escalate_offpath_after_irreversible` | The state went off path after an action that cannot be undone.                                                      |
| `escalate_unconfirmed_irreversible`   | A committing action could not be confirmed or proved absent.                                                        |
| `max_steps`                           | The loop reached its step cap.                                                                                      |

## Viewer transport events

The loop emits only the 13 kinds above. `jevonly.viewer.server` wraps each stored event with `seq`, `kind`, and Unix timestamp `t`, then sends it over SSE. It also creates three viewer-only kinds:

| Kind     | Fields                                                                                                                  | Purpose                                                                                       |
| -------- | ----------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `jev`    | `tag`, `latency_s`, `in_tok`, `retries`, `questions`, `answers`, `state`, `tokens_total`, `calls_total`, `cost_est_usd` | Shows every closed question, supplied option, probability, state, latency, and running usage. |
| `error`  | `error`, `traceback`                                                                                                    | Reports an exception from the worker thread.                                                  |
| `closed` | `tokens_total`, `calls_total`, `cost_est_usd`                                                                           | Marks worker cleanup and closes live UI controls.                                             |

All viewer records also receive `seq`, `kind`, and `t` when serialized by `/events`.
