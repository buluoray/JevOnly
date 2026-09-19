# FAQ

## 1. What does a run cost?

Cost scales with the page state sent per Jev call and the number of calls. The viewer counts input tokens and estimates cost at `$0.042` per million input tokens. A recorded 11-step Wikipedia comparison used 43 calls, about 340k tokens, and approximately $0.014. Viewport-only mode and a lower element cap reduce each state; a difficult form or keyboard-heavy task increases calls.

## 2. Why was a step undone?

JevOnly asks whether the action had its intended effect. A reversible action below the `0.50` verification threshold is retried or undone when the page changed. The loop also undoes the last reversible action when the current state reaches the `0.60` off-path threshold, or when navigation lands on a page with nothing actionable. Expand the step's Jev and verification rows to see the before/after question, score, threshold, and outcome.

## 3. Why did `none` win?

Every action vote includes `none`: “No listed action should be taken next: the task is already complete, or this state cannot advance it.” A strong `none >= 0.80` can stop when done is at least `0.35`. A weaker `none` must lead three consecutive plans before JevOnly gives up, and actions below `0.15` are not taken from a `none`-led ranking. With a code-owned completion check, JevOnly can refuse the stop and show Jev the unmet conditions twice.

## 4. How does copy choose a value without generating one?

JevOnly first asks which verbatim goal clause needs a value. It groups page text in reading order, asks which group contains the value, narrows repeatedly, then offers pieces cut from the final line: prices, times, dates, codes, numbers with units, capitalized phrases, words, and the full line. A final probability question checks that the piece has the right kind, subject, unit, and completeness. Only then is it stored as `copied_N`.

## 5. How do I add a fixture?

Put deterministic fixture content under `fixtures/`, then add the exercising test under `tests/` (Python) or `tests/js/` (Node). Keep fixture behavior explicit through accessible names, labels, roles, values, and visible result text so snapshot-based assertions do not depend on CSS selectors. Browser-launching tests should remain opt-in behind `JEVONLY_E2E=1`; unit tests can exercise text segmentation, question construction, event shape, and protocol dispatch without Chromium.

Local pages work in the viewer too: serve them with `scripts/serve-fixtures.py` and use the printed `http://127.0.0.1:<port>/...` URL as the start page.

## 6. Why not use a general LLM as the planner?

A general planner can produce an action or value that was never present in the observed state. JevOnly instead makes code enumerate the legal move set and asks Jev for probabilities over that set. This yields a complete audit trail—every option, probability, pick, action, and verification—while ensuring that typed values came from the goal, supplied facts, or page text.

## 7. What does “off path” mean?

It means the current state cannot move the goal forward: for example, an unrelated promotion, wrong section, error page, or loss of prior progress. It does not mean merely intermediate or unfamiliar. At `offpath >= 0.60`, JevOnly undoes the last reversible action and rejects that action at the prior page state. If the preceding action was irreversible, JevOnly escalates instead of pretending it can undo it.

## 8. What happens on a CAPTCHA or bot-check page?

JevOnly does not solve or bypass challenges. If the page exposes no usable controls after two short waits, the loop treats it as a wall, goes back when safe, and stops offering links to that host for the run. You can run headed Chromium to inspect the challenge, but do not interact with the browser while an automated run is active. Use sites that permit automation and follow their terms.

## 9. Can JevOnly type free text?

No—not text invented by the model. A field can receive an exact caller-supplied fact, a deterministic span cut from the goal (a name, a code, a date), a stretch of the goal's own words that Jev narrows down to -- clause first, then trimming an end or halving, stopping when it says the text is exactly the value, so `tallest building in Portland, Oregon` is pointed at, never composed -- or a checked value copied from the page. When none of those fits, the field is left alone rather than typed letter by letter. Code spells a selected known value while Jev chooses bounded controls such as next word, rest, stop, or a key from the fixed keyboard alphabet. If the required value does not exist in those sources, add it as a fact or rewrite the goal to include it.

## 10. Why did JevOnly stop without completing the goal?

Check the `end.stopped` reason and final timeline card:

| Reason family                        | Interpretation                                            | Next step                                                                             |
| ------------------------------------ | --------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `gave_up_none_streak`                | No supplied action had enough support for three plans.    | Make the goal more concrete, inspect missing controls, or enable viewport navigation. |
| `values_in_hand`                     | All values the goal asks for were copied.                 | Nothing; this is the normal end of a look-up goal.                                    |
| `gave_up_checklist_unmet`            | The model wanted to stop but the code check stayed false. | Inspect the unmet checklist and preceding rejected actions.                           |
| `no_candidates` / `site_unavailable` | No actionable control was available.                      | Confirm the page loaded and permits automation.                                       |
| `cycle` / `stalled_in_widget`        | The run repeated a state or one widget family.            | Tighten the goal or start closer to the task surface.                                 |
| `budget_exhausted` / `max_steps`     | A configured limit ended the run.                         | Inspect progress before increasing the relevant bound.                                |
| `escalate_*`                         | Safety logic could not safely retry or undo.              | Verify the external state manually before any retry.                                  |

Without a code-owned completion check, `stopped_on_done_signal` is Jev's judgment, not a verified postcondition. Add a visible-text or URL-contains completion check when the destination has a stable signal.
