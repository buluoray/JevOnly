# JevOnly examples

These examples are task descriptions for `jevonly run`. Install JevOnly, install the Playwright browser, and set a TypeSafe API key before running a live example:

```sh
python3 -m pip install -e '.[dev]'
playwright install chromium
export TYPESAFE_API_KEY=tsk_your_key_here
```

The JSON files in `goals/` record the goal, starting page, known facts, step limit, and expected outcome. The CLI accepts those values as flags rather than accepting the JSON file directly.

## Live web examples

### Compare Wikipedia building heights

```sh
jevonly run \
  --goal 'Find the tallest building in Seattle on Wikipedia, note its height in meters, then find the tallest building in Portland, Oregon, and stop when you can compare the two heights.' \
  --start 'https://en.wikipedia.org' \
  --max-steps 28
```

Expected outcome: JevOnly navigates to the relevant articles, copies both metric heights, and stops once the two values can be compared. A representative run took about 11 steps, 43 Jev calls, 340,000 tokens, $0.014, and 17 seconds.

### Find a filtered one-way flight

```sh
jevonly run \
  --goal 'Find one-way flights from Boston to Paris CDG on December 5, 2026 for one adult in premium economy. Include nearby airports for the departure, show only Air France flights, and stop when you can read the departure time and price of the earliest Air France flight. Do not change the currency or the language.' \
  --start 'https://www.google.com/travel/flights' \
  --max-steps 40
```

Expected outcome: JevOnly configures the route, date, passenger count, cabin, nearby-airport option, and airline filter, then copies the earliest matching departure time and price. A representative run took about 21 steps, 82 Jev calls, 360,000 tokens, $0.015, and 40 seconds.

Live pages change. Treat the measurements as observed examples, not performance guarantees.

## Offline fixtures

Start the fixture server on a known port:

```sh
python3 scripts/serve-fixtures.py --port 8000
```

In another terminal, run any offline example:

```sh
jevonly run \
  --goal 'Read the Harbor Tower article and stop when you can report its height in meters. Do not confuse the height with the year it was completed.' \
  --start 'http://127.0.0.1:8000/infobox.html' \
  --max-steps 8

jevonly run \
  --goal 'Search for trips to Lakeview on December 5, 2026, submit the form, and stop when you can read the earliest departure time and price.' \
  --start 'http://127.0.0.1:8000/search-form.html' \
  --max-steps 16

jevonly run \
  --goal 'Complete the setup wizard with Focus mode and Weekly updates, then stop when the completion confirmation is visible.' \
  --start 'http://127.0.0.1:8000/multistep.html' \
  --max-steps 14
```

Expected outcomes are, respectively: copy `286 m` instead of the nearby year; expose the `7:15 AM` and `$184` cells in the first result row; and reach the wizard confirmation after selecting both requested options.

Run `./scripts/demo.sh` to start a server on a free port and print all three commands. If `TYPESAFE_API_KEY` is set, the script also runs the infobox example.

The same `http://127.0.0.1:<port>/...` URLs work as the start page in the viewer (`jevonly serve`).
