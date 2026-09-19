# Contributing to JevOnly

JevOnly is a browser agent with a strict boundary: **the code builds the options, and the model only picks**. A change that lets the model write free text into a field or a URL is out of scope. Every pull request must preserve this invariant.

## Development setup

JevOnly requires Python 3.12+ and Node.js 18+.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
npm install
make test
make lint
```

Run the opt-in browser tests with a local Chromium installation:

```sh
JEVONLY_E2E=1 make test
```

## Page fixtures

Add deterministic, self-contained page fixtures under `fixtures/`. Keep them small and free of network dependencies, credentials, and personal data. Give interactive controls accessible names so the browser snapshot represents what a user can perceive. Add a focused pytest or `node:test` case that demonstrates the relevant observation, action, and verification behavior.

A fixture should model one browser behavior, not reproduce an entire third-party site. Replace account details, addresses, identifiers, and page content with synthetic values.

## Questions and choices

Before adding a question, identify the closed decision the loop cannot already make. Define its options in code, document the criteria that distinguish them, and test the resulting question shape. Do not add open-ended prompts or any path that turns model output into typed text, a selector, JavaScript, or a URL.

New actions must remain inspectable in the event timeline and must follow the existing verification, retry, undo, and irreversible-action controls.

## Commits and pull requests

Use Conventional Commits, for example `fix: retry the next-best visible control` or `docs: explain copy-register verification`.

Keep pull requests focused. Include:

- a concise description of the user-visible or protocol behavior;
- tests for the changed decision boundary;
- whether end-to-end browser tests ran, and why if they did not;
- documentation updates for changed commands, events, questions, or safety behavior.

Reviewers check that choices come from deterministic code, probabilities are not treated as generated content, browser actions remain reversible where possible, irreversible actions remain gated, and logs do not expose secrets or personal data. Address review comments with code or evidence from a targeted test.