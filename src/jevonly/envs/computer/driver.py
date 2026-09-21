"""The desktop driver the computer environment talks to, and its Kiro Crew implementation.

The environment never touches an accessibility API. It sees a :class:`Tree` (one window, its elements
in walk order) and asks the driver to press, set or type against an element *of a tree it was shown*.
Every mutation therefore carries the tree it came from: the driver re-walks the window and refuses when
the element at that index is no longer the same control (drift), then waits for the UI to settle and
returns the new tree. Element indices are meaningful only against the walk that produced them; the
environment never searches a fresh tree for "something similar" to act on.

:class:`KiroCrewDriver` is the first implementation. It does NOT wrap the raw platform backend: it runs
the same governed steps Kiro Crew's own ``computer_*`` tools run, in the same order -- primary enable,
operator app policy, drift verification against a fresh walk, secure-field and sensitive-text refusal
for anything typed, settle-then-refresh, SEL audit -- through the same ``service`` / ``policy`` /
``gate`` modules. Kiro Crew's Linux backend is a typed refusal, so this driver is live on macOS and
Windows only. A fake driver (tests) implements the same protocol from recorded trees.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

SESSION_KEY = "jevonly"  # the surface identity Kiro Crew's audit records for every call this driver makes

# A settle is "two consecutive walks agree". These bound the wait so a window that never stops
# animating (a progress bar, a clock) still yields a tree instead of hanging the step.
SETTLE_POLL_SECS = 0.12
SETTLE_CAP_SECS = 2.5


class DriverError(RuntimeError):
    """A refusal or failure the driver reports; the environment turns it into ``last_action_error``."""


class DriverUnavailable(DriverError):
    """The driver cannot run at all here (disabled, unsupported platform, missing package)."""


class Stale(DriverError):
    """The element the environment meant to act on is no longer at its index (the tree moved)."""


@dataclass(frozen=True)
class Elem:
    """One node of the window's accessibility tree, addressed by ``index`` within its :class:`Tree`."""

    index: int
    role: str  # short role: button, textfield, checkbox, menuitem, row, statictext ...
    title: str = ""
    value: str = ""
    actions: tuple[str, ...] = ()  # the element's own advertised actions (AXPress, AXConfirm ...)
    depth: int = 0
    enabled: bool = True
    secure: bool = False  # a password box: never listed, never typed into, value never read
    editable: bool = False
    focused: bool = False
    selected: bool = False
    expanded: bool = False
    frame: tuple[float, float, float, float] | None = None  # window-local (x, y, w, h) or None
    path: tuple[str, ...] = ()  # titles of the titled ancestors, outermost first


@dataclass
class Tree:
    """One walk of one window. ``token`` distinguishes walks so a stale one is detectable."""

    app_key: str  # stable identity of the window (pid + window id); the "url" of a desktop state
    app_name: str
    window_title: str
    elements: list[Elem]
    selected_text: str = ""
    truncated: bool = False
    token: int = 0
    text_units: list[str] = field(default_factory=list)


class Driver(Protocol):
    """What the environment needs from a desktop. Every method raises :class:`DriverError` on refusal."""

    def resolve(self, query: str) -> Any:
        """The running app *query* names (display name or bundle id). Raises when none or several match."""

    def launch(self, query: str) -> Any:
        """Open the installed app *query* names and wait for its window. Raises if it already has one."""

    def snapshot(self, app: Any) -> Tree: ...

    def press(self, app: Any, tree: Tree, index: int) -> Tree: ...

    def set_value(self, app: Any, tree: Tree, index: int, value: str) -> Tree: ...

    def type_text(self, app: Any, tree: Tree, index: int, text: str) -> Tree: ...

    def press_key(self, app: Any, tree: Tree, index: int, key: str) -> Tree: ...

    def scroll(self, app: Any, tree: Tree, index: int, direction: str) -> Tree: ...

    def end(self) -> None: ...


class KiroCrewDriver:
    """The governed Kiro Crew computer-use layer as a :class:`Driver`.

    Mirrors ``kiro_crew.computer_use.tools._dispatch`` + ``_run`` step for step. The order matters and
    is the same there: enable -> resolve app -> audit -> operator app policy -> (mutation only) drift
    check against a fresh walk -> input-target policy -> act -> settle -> refresh.
    """

    def __init__(self, session_key: str = SESSION_KEY):
        try:
            from kiro_crew.computer_use import enable_state, gate, policy, service
            from kiro_crew.computer_use import types as kc_types
        except ImportError as exc:  # pragma: no cover - depends on the host install
            raise DriverUnavailable(
                "the computer environment needs Kiro Crew installed in this interpreter (package kiro_crew)"
            ) from exc
        self._enable_state, self._gate, self._policy, self._service, self._t = (
            enable_state,
            gate,
            policy,
            service,
            kc_types,
        )
        state = enable_state.load_state()
        if not enable_state.is_enabled(state):
            raise DriverUnavailable("computer use is switched off in Kiro Crew (Settings -> Computer Use)")
        self._cfg = enable_state.load_policy_config(state)
        self._svc = service.get_shared_service()
        status = self._svc.status()
        if not status.supported:
            raise DriverUnavailable(f"computer use is not supported on this platform ({status.reason})")
        self._session_key = session_key
        self._req = service.snapshot_request(want_image=False)
        self._token = 0

    # -- apps --

    def resolve(self, app_query: str):
        try:
            app = self._svc.resolve_app(app_query)
        except Exception as exc:  # noqa: BLE001 - the service raises its own family; all are refusals here
            raise DriverError(str(exc)) from exc
        self._admit(app, "computer_get_state")
        return app

    def launch(self, app_query: str):
        denial = self._gate.require_computer_use(
            "computer_launch_app", session_key=self._session_key, app=app_query, requires_app_identity=False
        )
        if denial:
            raise DriverError(denial)
        # The same three policy passes the governed tool makes: the raw name (repeated into every
        # field check_app reads), each RESOLVED identity before any process exists, and the process
        # the OS actually reported once its window is up.
        probe = self._t.AppRef(name=app_query, pid=0, bundle_id=app_query, window_title=app_query)
        refusal = self._policy.check_app(probe, self._cfg)
        if refusal:
            raise DriverError(refusal)
        try:
            text, app = self._svc.launch_app(
                app_query,
                permit=lambda who: self._policy.check_app(who.as_app_ref(), self._cfg),
                refuse_launched=lambda ref: self._policy.check_app(ref, self._cfg),
            )
        except Exception as exc:  # noqa: BLE001
            raise DriverError(str(exc)) from exc
        if app is None:
            # The process started but showed no window yet; launching again would open a second copy.
            raise DriverError(f"launched, but no window appeared yet: {text}")
        self._admit(app, "computer_launch_app")
        return app

    def _admit(self, app, action: str) -> None:
        """The audit line plus the operator's app policy, exactly as the governed tools apply them."""
        denial = self._gate.require_computer_use(
            action,
            session_key=self._session_key,
            app=app.name,
            app_bundle_id=app.bundle_id,
            app_display_name=app.name,
        )
        if denial:
            raise DriverError(denial)
        refusal = self._policy.check_app(app, self._cfg)
        if refusal:
            raise DriverError(refusal)

    # -- observation --

    def snapshot(self, app) -> Tree:
        try:
            snap = self._svc.snapshot(app, self._req, session_key=self._session_key)
        except Exception as exc:  # noqa: BLE001
            raise DriverError(str(exc)) from exc
        return self._tree(app, snap)

    def _tree(self, app, snap) -> Tree:
        self._token += 1
        titled: list[tuple[int, str]] = []  # (depth, title) stack of titled ancestors
        elems = []
        for rec in snap.elements:
            while titled and titled[-1][0] >= rec.depth:
                titled.pop()
            elems.append(
                Elem(
                    index=rec.index,
                    role=rec.short_role,
                    title="" if rec.secure else rec.title,
                    value="" if rec.secure else rec.value,
                    actions=tuple(rec.actions),
                    depth=rec.depth,
                    enabled=rec.enabled,
                    secure=rec.secure,
                    editable=self._t.TRAIT_EDITABLE in rec.traits,
                    focused=rec.focused,
                    selected=self._t.TRAIT_SELECTED in rec.traits,
                    expanded=self._t.TRAIT_EXPANDED in rec.traits,
                    frame=rec.frame,
                    path=tuple(t for _, t in titled),
                )
            )
            if rec.title and not rec.secure:
                titled.append((rec.depth, rec.title))
        return Tree(
            app_key=f"{app.pid}/{app.window_id}",
            app_name=app.name,
            window_title=snap.window_title,
            elements=elems,
            selected_text=snap.selected_text,
            truncated=snap.truncated or snap.depth_truncated,
            token=self._token,
        )

    # -- mutation --

    def _rec(self, app, tree: Tree, index: int):
        """The Kiro Crew record for *index*, verified against a FRESH walk (drift check) first."""
        try:
            cached = self._svc.cached(app, session_key=self._session_key)
            rec = self._svc.element(cached, index)
            self._svc.verify_fingerprint(app, rec, self._req, session_key=self._session_key)
        except self._t.StaleIndex as exc:
            raise Stale(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise DriverError(str(exc)) from exc
        return rec

    def _typed(self, app, rec, text: str) -> None:
        refusal = self._policy.check_input_target(app, rec, text, self._cfg)
        if refusal:
            raise DriverError(refusal)

    def _after(self, app) -> Tree:
        """Settle, then refresh: wait until two consecutive walks agree (bounded), return the last."""
        time.sleep(self._t.POST_ACTION_SETTLE_SECS)
        deadline = time.monotonic() + SETTLE_CAP_SECS
        prev = self.snapshot(app)
        while time.monotonic() < deadline:
            time.sleep(SETTLE_POLL_SECS)
            cur = self.snapshot(app)
            if _shape(cur) == _shape(prev):
                return cur
            prev = cur
        return prev

    def _do(self, app, tree: Tree, index: int, fn, *args) -> Tree:
        rec = self._rec(app, tree, index)
        try:
            fn(app, rec, *args)
        except Exception as exc:  # noqa: BLE001
            raise DriverError(str(exc)) from exc
        return self._after(app)

    def press(self, app, tree: Tree, index: int) -> Tree:
        req = self._t.ClickRequest(method=self._t.CLICK_METHOD_ACCESSIBILITY)
        return self._do(app, tree, index, lambda a, r: self._svc.click(a, r, req))

    def set_value(self, app, tree: Tree, index: int, value: str) -> Tree:
        rec = self._rec(app, tree, index)
        self._typed(app, rec, value)
        try:
            self._svc.set_value(app, rec, value)
        except Exception as exc:  # noqa: BLE001
            raise DriverError(str(exc)) from exc
        return self._after(app)

    def type_text(self, app, tree: Tree, index: int, text: str) -> Tree:
        rec = self._rec(app, tree, index)
        self._typed(app, rec, text)
        try:
            self._svc.type_text(app, rec, text)
        except Exception as exc:  # noqa: BLE001
            raise DriverError(str(exc)) from exc
        return self._after(app)

    def press_key(self, app, tree: Tree, index: int, key: str) -> Tree:
        return self._do(app, tree, index, lambda a, r: self._svc.press_key(a, r, key))

    def scroll(self, app, tree: Tree, index: int, direction: str) -> Tree:
        return self._do(app, tree, index, lambda a, r: self._svc.scroll(a, r, direction, 1.0))

    def end(self) -> None:
        self._svc.end_turn(session_key=self._session_key)


def _shape(tree: Tree) -> tuple:
    """What "the UI settled" compares: structure and values, not the walk token."""
    return (tree.window_title, tuple((e.role, e.title, e.value, e.enabled, e.expanded) for e in tree.elements))
