"""Environments the core loop can drive. Each one implements ``jevonly.core.Environment``.

The registry maps a task's ``env`` name to a factory; ``register`` adds one without touching the core."""

from collections.abc import Callable

_REGISTRY: dict[str, Callable] = {}


def register(name: str, factory: Callable) -> None:
    """Make ``factory(task)`` the environment for tasks whose ``env`` is ``name``."""
    _REGISTRY[name] = factory


def make(task: dict):
    """Build the environment a task asks for (``task["env"]``, default ``browser``)."""
    name = task.get("env", "browser")
    if name not in _REGISTRY:
        if name == "browser":
            from .browser import BrowserEnv  # imported on demand: the browser needs Node and Playwright

            register("browser", BrowserEnv)
        elif name == "computer":
            from .computer import ComputerEnv  # imported on demand: the desktop needs Kiro Crew installed

            register("computer", ComputerEnv)
        else:
            raise RuntimeError(
                f"no environment registered under {name!r}; known: {sorted(set(_REGISTRY) | {'browser', 'computer'})}"
            )
    return _REGISTRY[name](task)


__all__ = ["make", "register"]
