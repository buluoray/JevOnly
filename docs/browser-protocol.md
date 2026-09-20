# Browser child protocol

JevOnly launches `src/jevonly/envs/browser/js/browser_server.js` with Node and communicates over standard input and standard output. Each input line is one JSON object. Each output line is exactly one JSON object replying to that command. Standard error is reserved for diagnostics and is not part of the protocol.

The child launches Chromium before reading commands. Playwright is resolved in this order:

1. the module or directory named by `JEV_PLAYWRIGHT`;
2. package-local `node_modules/playwright` locations;
3. Node's normal `require("playwright")` resolution.

`JEVONLY_NODE` is interpreted by the Python launcher, not by the child. Browser launch behavior is controlled by:

- `JEV_CHROME_CHANNEL`: a Playwright Chromium channel such as `chrome`; the bundled Chromium is used if the channel cannot launch.
- `JEV_PROFILE_DIR`: persistent browser profile directory.
- `JEV_HEADED=1`: launch a visible browser instead of headless Chromium.
- `JEV_FRAMES`: Unix-domain socket receiving newline-delimited screencast frame objects independently of standard output.

The launcher stops the child with `quit`. Closing standard input also closes the browser. A `quit` reply is written before browser shutdown, which is capped at five seconds.

## Response and error rules

Successful replies contain `{"ok":true}` and any command-specific fields. Failed replies contain `{"ok":false,"error":"..."}`. A malformed input line returns `bad json`; an unsupported command returns `unknown cmd`. Uncaught command errors are converted to an `error` string capped at 300 characters. Action error strings are capped at 160 characters and use the `action failed: ` prefix.

Candidate indices come from the latest `snapshot`. A missing or re-rendered candidate returns `stale candidate`. Callers must obtain a new snapshot after navigation or material page changes rather than reusing stale indices.

## Settle rule

Actions do not wait for the page. `goto`, `act`, `truncate_fill` and `keyboard` reply as soon as the
action itself has been performed (for `goto`, as soon as `domcontentloaded` fires), so the caller can
observe and judge at once. The child keeps track of the requests each action started; the caller decides
when a wait is worth paying by asking:

- `inflight` -- how many requests started by the last action are still in flight (instant);
- `wait_inflight` -- wait, up to `cap_ms` (default 3000), while such requests remain in flight.

Requests that predate the action by more than 50 ms do not count. WebSocket, EventSource and ping
traffic do not count. `wait_inflight` replies with `settled_ms` (milliseconds waited) and `settled`
(`true` when no relevant request remained, `false` when the cap was reached), plus `pending`.

Every action reply also carries `settled_ms` and `settled` for the wait it performed itself, which is
zero by default.

`snapshot` has one bounded wait of its own: while CSS animations or transitions are running (a menu
closing, a dialog fading in) it waits up to 400 ms (`transition_cap_ms`) for them to end, and reports
the wait as `transition_ms`. An observation taken mid-transition shows the old state half-drawn, and a
judgment on it is wrong in a way no retry fixes. A page that is not animating pays nothing. Two exceptions keep a bounded wait of their own:

| Command path                          | Default wait                                                                                                          |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `keyboard`                            | only while requests fired by the keystroke (an autocomplete lookup) are in flight, up to `settle_ms` (default 400 ms) |
| `act` recovery after a covered target | 350 ms after dismissing the popover, before the second click                                                          |

`settle_ms`, `act_settle_ms` and `settle_cap_ms` restore a fixed floor / cap per command for callers
that want the child to wait before replying.

### `inflight`

```json
{ "cmd": "inflight" }
```

Success: `ok`, `pending` (number of requests started by the last action still in flight).

### `wait_inflight`

```json
{ "cmd": "wait_inflight", "cap_ms": 3000 }
```

- `cap_ms` (number, optional, default 3000): longest wait.

Success: `ok`, `pending`, `settled_ms`, `settled`.

## Commands

### `goto`

Navigate the active page.

Input:

```json
{ "cmd": "goto", "url": "https://example.com" }
```

- `url` (string, required): destination URL.
- `settle_ms`, `settle_cap_ms` (number, optional): a fixed wait before replying; zero by default (see the settle rule).

Success: `ok`, `settled_ms`, `settled`.

Errors include navigation timeout, invalid URL, and browser target closure.

### `snapshot`

Observe page text and enabled interactive controls. The child stamps DOM candidates with `data-jev-cand` indices used by later commands.

Input:

```json
{
  "cmd": "snapshot",
  "max_cands": 120,
  "text_budget": 6000,
  "ctx_budget": 400,
  "prefer_main": false,
  "viewport_only": true
}
```

All arguments are optional:

- `max_cands`: candidate cap; default 30.
- `text_budget`: visible-text character budget; default 900.
- `ctx_budget`: per-candidate surrounding-context budget; default 140.
- `prefer_main`: place controls in navigation/header/footer after main controls.
- `viewport_only`: include only controls and text visible in the viewport and add scroll pseudo-candidates.

Success contains `snapshot` with:

- `url`, `title`, `headings`, `visible_text`, `text_units` -- the on-screen text as short units in reading order: one unit per short block (a row, a list item, a paragraph), a table cell as its own unit labelled with its row and column headers (`Price · Anker Nano 30W: $18.24`), and a bare text node otherwise;
- `candidates`: objects with `role`, `name`, `ctx`, `tag`, and `fam`, plus applicable `hint`, `host`, `pseudo`, `value`, `placeholder`, `options`, `checked`, and `expanded` fields;
- `dialogs`: dialogs dismissed since the prior snapshot;
- `total_interactive`, `modal`, `omitted`, and `truncated_runs`.

A modal limits candidates and text to its working surface. Disabled controls are excluded. Repeated control families are moved behind one-off controls before the cap is applied.

### `act`

Perform an action selected from the latest snapshot.

Input:

```json
{ "cmd": "act", "index": 0, "action": "click", "value": "optional" }
```

- `index` (integer): candidate index. Not used by `scroll` or `find`.
- `action`: `click`, `fill`, `fill_enter`, `select`, `check`, `scroll`, or `find`.
- `value`: text, select label, scroll direction (`up` or `down`), or find query.
- `act_settle_ms`, `settle_cap_ms`: settle overrides.

Ordinary success: `ok`, `url`, `new_tab`, `recovered`, `settled_ms`, `settled`.

- `new_tab` is true when the action opened and adopted a tab.
- `recovered` is `"escape"` when an obstructed click succeeded after dismissing a popover; otherwise it is `null`.

`scroll` success contains `ok`, `url`, `settled_ms`, `settled`. `find` also contains `found:true`. A missing find query returns `action failed: "..." is not on this page`.

`fill` and `fill_enter` type autocomplete comboboxes into the focused overlay input. `fill_enter` commits the first visible option when possible. `select` matches an option by visible label. `check` sets the target checked.

### `keyboard`

Send one key or a short text to the focused element.

Input:

```json
{ "cmd": "keyboard", "focus": true, "index": 0, "key": "Enter", "text": "unused", "settle_ms": 400 }
```

- `focus`: click the candidate before typing.
- `index`: candidate to focus when `focus` is true.
- exactly one of `key` or `text` should be supplied; `key` takes precedence.
- `settle_ms`: longest wait for requests the keystroke fired (default 400 ms).
- `settle_cap_ms` is accepted for caller compatibility and ignored.

Success: `ok`, `typed`, `cursor`, `sel_end`, `options`. `options` contains up to eight visible ARIA option labels.

### `back`

Undo navigation.

Input: `{"cmd":"back"}`.

If the active page was adopted from a new tab, the child closes it and returns to its opener. Success then contains `ok`, `url`, `closed_tab:true`. Otherwise it performs browser back with a five-second navigation cap and returns `ok`, `url`.

### `screenshot`

Capture the active viewport as JPEG.

Input:

```json
{ "cmd": "screenshot", "quality": 55, "highlight": 0, "keep": false }
```

- `quality`: JPEG quality; default 55.
- `highlight`: optional candidate index outlined before capture.
- `keep`: retain the outline until a later action clears it.

Success: `ok`, `jpeg_b64`, `url`. Capture failure returns `screenshot failed`.

### `inject_popup`

Add a deterministic unrelated modal used by fault-injection tests.

Input: `{"cmd":"inject_popup"}`. Success: `{"ok":true}`.

### `truncate_fill`

Fill only the first 60% of a requested value, with a minimum of one character. This command supports partial-success fault testing.

Input:

```json
{ "cmd": "truncate_fill", "index": 0, "action": "fill_enter", "value": "example" }
```

Success: `ok`, `landed`, `url`, `new_tab`, `settled_ms`, `settled`. `landed` is the partial text written.

### `noop`

Do nothing. Input: `{"cmd":"noop"}`. Success: `{"ok":true}`.

### `probe`

Report what the page sees about its browser environment.

Input: `{"cmd":"probe"}`.

Success fields: `ok`, `webdriver`, `ua`, `cookies`, `headless`, `profile`, `channel`. `cookies` is the number of document-cookie entries, not cookie content.

### `quit`

Reply and stop the child.

Input: `{"cmd":"quit"}`. Reply: `{"ok":true}`. Browser close then runs with a five-second cap and the process exits successfully.
