#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

command -v node >/dev/null 2>&1 || {
  echo "JevOnly requires Node.js 22 or newer." >&2
  exit 1
}
command -v npm >/dev/null 2>&1 || {
  echo "JevOnly requires npm." >&2
  exit 1
}

node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)' || {
  echo "JevOnly requires Node.js 22 or newer; found $(node --version)." >&2
  exit 1
}

PYTHON=""
for candidate in python3.12 python3; do
  if command -v "$candidate" >/dev/null 2>&1 &&
    "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
    PYTHON="$(command -v "$candidate")"
    break
  fi
done

if [[ -z "$PYTHON" ]]; then
  echo "JevOnly requires Python 3.12 or newer." >&2
  exit 1
fi

cd "$ROOT"
if [[ ! -x .venv/bin/python ]]; then
  "$PYTHON" -m venv .venv
fi

.venv/bin/python -m pip install -e .
npm install --registry=https://registry.npmjs.org/
npx playwright install chromium

exec .venv/bin/jevonly serve "$@"
