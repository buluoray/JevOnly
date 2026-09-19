import importlib
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if (SRC / "jevonly").is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _install_legacy_shim() -> None:
    """Expose the single-file harness through the package contract until the split exists."""
    harness = ROOT / "harness" / "jev_harness_run.py"
    if not harness.is_file():
        raise ModuleNotFoundError("jevonly is not importable and the legacy harness is unavailable")

    spec = importlib.util.spec_from_file_location("_jevonly_legacy_harness", harness)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load legacy harness: {harness}")
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)

    package = types.ModuleType("jevonly")
    package.__path__ = []
    package.__package__ = "jevonly"
    sys.modules["jevonly"] = package
    for name in ("text", "copy", "questions", "keyboard", "loop", "jev"):
        sys.modules[f"jevonly.{name}"] = legacy
        setattr(package, name, legacy)


try:
    importlib.import_module("jevonly")
except ModuleNotFoundError as exc:
    if exc.name != "jevonly":
        raise
    _install_legacy_shim()

from fake_jev import FakeJev  # noqa: E402


class EventSink(list):
    def __call__(self, kind, payload):
        self.append({"kind": kind, **payload})


@pytest.fixture
def fake_jev():
    return FakeJev


@pytest.fixture
def events_sink():
    return EventSink()


@pytest.fixture
def jev_playwright():
    """Return the caller-provided Playwright module path without replacing it."""
    return os.environ.get("JEV_PLAYWRIGHT")


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: requires JEVONLY_E2E=1 and a Chromium installation")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("JEVONLY_E2E") == "1":
        return
    skip = pytest.mark.skip(reason="set JEVONLY_E2E=1 to run browser end-to-end tests")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip)
