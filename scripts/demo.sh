#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

coproc FIXTURE_SERVER { python3 "$ROOT_DIR/scripts/serve-fixtures.py" --port 0; }
SERVER_PID=$FIXTURE_SERVER_PID
IFS= read -r BASE_URL <&"${FIXTURE_SERVER[0]}"
cleanup() {
  kill "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

GOALS=(
  'Read the Harbor Tower article and stop when you can report its height in meters. Do not confuse the height with the year it was completed.'
  'Search for trips to Lakeview on December 5, 2026, submit the form, and stop when you can read the earliest departure time and price.'
  'Complete the setup wizard with Focus mode and Weekly updates, then stop when the completion confirmation is visible.'
)
URLS=(
  "$BASE_URL/infobox.html"
  "$BASE_URL/search-form.html"
  "$BASE_URL/multistep.html"
)
STEPS=(8 16 14)

printf 'Offline fixture server: %s\n\n' "$BASE_URL"
printf 'Example commands:\n'
for index in "${!GOALS[@]}"; do
  printf 'jevonly run --goal %q --start %q --max-steps %q\n' \
    "${GOALS[$index]}" "${URLS[$index]}" "${STEPS[$index]}"
done

if [[ -z ${TYPESAFE_API_KEY:-} ]]; then
  printf '\nTYPESAFE_API_KEY is not set; the commands were printed but no example was run.\n'
  exit 0
fi

if ! command -v jevonly >/dev/null 2>&1; then
  printf '\nThe jevonly command is not installed. From the repository root, run: python3 -m pip install -e .\n' >&2
  exit 1
fi

printf '\nRunning the infobox example...\n'
jevonly run --goal "${GOALS[0]}" --start "${URLS[0]}" --max-steps "${STEPS[0]}"
