import ast
from pathlib import Path

from jevonly.core.loop import EVENT_KINDS

MODULES = ("loop.py", "keyboard.py", "copy.py")


def emitted_kinds(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    kinds = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "emit":
            first = node.args[0]
            assert isinstance(first, ast.Constant) and isinstance(first.value, str), (
                f"emit kind must be a string literal in {path}:{node.lineno}"
            )
            kinds.add(first.value)
    return kinds


def test_event_kinds_is_frozen_and_covers_every_literal_emit():
    assert isinstance(EVENT_KINDS, frozenset)
    package = Path(__file__).parents[1] / "src" / "jevonly" / "core"
    emitted = set().union(*(emitted_kinds(package / name) for name in MODULES))

    assert emitted
    assert emitted <= EVENT_KINDS
