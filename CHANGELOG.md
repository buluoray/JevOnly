# Changelog

All notable changes to JevOnly will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Irreversible-action gate with `refuse` / `ask` / `allow` policies, an `approval` event, a viewer approval bar and `jevonly run --irreversible`.
- Per-task decision lines (`verify`, `offpath`, `done`, `risk`): viewer sliders and `jevonly run --threshold NAME=VALUE`.

### Added

- Closed-choice browser planning through Jev, with options constructed entirely by deterministic code.
- Chromium control through a JSON-lines Node.js bridge and accessibility snapshots.
- Goal, page-unit, keyboard, scroll, find, and copy-register decision paths.
- Verification, retry, undo, off-path recovery, and irreversible-action gating.
- Loopback live viewer with an event timeline, replay, GIF export, and copy log.
- Command-line `run` and `serve` workflows.
