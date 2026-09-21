"""A desktop application as a JevOnly environment.

The loop sees the same shape it sees for a web page: a state with a URL-like identity, the controls a
person could act on as candidates, the text on screen as units to copy from. Under it is one window of
one native app, read through a :class:`~jevonly.envs.computer.driver.Driver` (Kiro Crew's governed
computer-use layer by default).

What differs from the browser, and is deliberately not papered over:

* **Indices are snapshot-scoped.** A candidate carries the walk index and the walk token it came from;
  acting on a candidate from an older walk is refused, and the driver re-walks and refuses on drift
  before every mutation. The environment never searches a new tree for a look-alike to act on.
* **Undo is compensating, or it is reported as failed.** A written field is restored to the value it
  held; a press that opened a menu is closed with Escape; anything else returns ``restored=False`` and
  the loop stops rather than pretending the state went back. There is no generic Cmd+Z: it would undo
  the user's own last edit.
* **No whole-form batch.** Desktop fields re-render on edit (validation, disclosure, focus), so fields
  are filled one at a time, each drift-checked; ``supports_atomic_batch`` is False and the loop does not
  offer ``fill_form`` here.
* **No coordinates, no pointer.** Every action is an accessibility action on a named element.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
from typing import Any

from .driver import DriverError, Elem, KiroCrewDriver, Stale, Tree

# Roles that take a press. AXPress in ``actions`` is the primary signal; these roles are pressed even
# when an app forgets to advertise it (Electron shells often do).
CLICK_ROLES = frozenset(
    {
        "button",
        "checkbox",
        "radiobutton",
        "menuitem",
        "menubaritem",
        "link",
        "tab",
        "popupbutton",
        "menubutton",
        "disclosuretriangle",
        "row",
        "cell",
        "outlinerow",
        "incrementor",
        "colorwell",
    }
)
TEXT_ROLES = frozenset({"textfield", "textarea", "searchfield", "combobox", "securetextfield"})
SCROLL_ROLES = frozenset({"scrollarea", "scrollbar"})
TEXT_ONLY_ROLES = frozenset({"statictext", "heading"})
CONTAINER_ROLES = frozenset({"row", "cell", "outlinerow", "group", "list", "table", "outline"})

# Labels whose press the loop must treat as irreversible without asking Jev: the risk question is a
# score, and these are the verbs where a wrong 0.4 costs the user data. Word-boundary, case-insensitive.
IRREVERSIBLE_LABEL = re.compile(
    r"\b(delete|remove|erase|discard|send|purchase|buy|pay|order now|place order|empty trash|"
    r"shut down|restart|log out|sign out|format|reset|uninstall|move to trash|don't save|do not save)\b",
    re.IGNORECASE,
)

KEY_NAMES = {  # the loop's browser-style key names -> Kiro Crew key specs
    "Escape": "escape",
    "Enter": "return",
    "Backspace": "backspace",
    "Delete": "forwarddelete",
    "Tab": "tab",
    "ArrowLeft": "left",
    "ArrowRight": "right",
    "ArrowUp": "up",
    "ArrowDown": "down",
    "Home": "home",
    "End": "end",
}

MAX_UNIT_LEN = 200
CTX_BUDGET = 80
UNITS_BUDGET = 400


class ComputerEnv:
    """One native application window, driven through accessibility actions only."""

    supports_atomic_batch = False  # fields re-render on edit; the loop fills them one at a time here

    def __init__(self, task, driver=None):
        self.task = task
        query = str(task.get("app") or "").strip()
        if not query:
            raise RuntimeError("a computer task names the application to drive: task['app'] (e.g. 'Notes')")
        self.driver = driver if driver is not None else KiroCrewDriver()
        try:
            self.app = self.driver.resolve(query)
        except DriverError:
            if not task.get("launch"):
                raise
            self.app = self.driver.launch(query)
        self._tree: Tree | None = None
        self._after: Tree | None = None  # the tree an action returned, consumed by the next observe()
        self._prev_tree: Tree | None = None
        self._pending: list[dict] = []  # undo frames recorded since the last observe()
        self._frames: list[dict] = []  # the frames the next undo() restores (the previous step's)
        self._ctx_budget = CTX_BUDGET
        self._units_budget = UNITS_BUDGET
        self._snap: dict[str, Any] = {}
        self._focus_index = None  # the field keyboard mode is typing into, within the current tree
        self.last_action_error = None
        self.last_action_note = None
        self.last_settled = None

    # -- observation --

    def _refresh(self) -> Tree:
        if self._after is not None:
            tree, self._after = self._after, None
        else:
            tree = self.driver.snapshot(self.app)
        self._prev_tree, self._tree = self._tree, tree
        return tree

    def observe(self):
        tree = self._refresh()
        # A step boundary: what was done since the last observation is what the next undo restores.
        self._frames, self._pending = self._pending, []
        self._focus_index = None
        cands = self._interactive(tree)
        elements = []
        for e in cands:
            ctx = self._context(e)
            row = {"id": self._id(tree, e), "role": e.role, "name": e.title}
            if ctx:
                row["context"] = ctx[: self._ctx_budget]
            if e.value and not e.secure:
                row["value"] = e.value
            if e.role in ("checkbox", "radiobutton", "menuitem"):
                row["checked"] = e.selected or e.value in ("1", "true", "on")
            if e.expanded:
                row["expanded"] = True
            elements.append(row)
        units = self._units(tree)
        self._snap = {"text_units": units, "title": tree.window_title, "url": self._url(tree)}
        obs = {
            "url": self._url(tree),
            "title": tree.window_title,
            "headings": [t for t in dict.fromkeys([tree.window_title, *self._group_titles(tree)]) if t],
            "visible_text": "\n".join(units)[: self._units_budget * 40],
            "elements": elements,
        }
        if tree.truncated:
            obs["elements_not_listed"] = "the window has more controls than were read; scroll to reach them"
        return obs

    def _url(self, tree: Tree) -> str:
        host = re.sub(r"[^a-z0-9.-]+", "-", tree.app_name.lower()).strip("-") or "app"
        return f"app://{host}/{tree.app_key}"

    @staticmethod
    def _group_titles(tree: Tree):
        return [
            e.title for e in tree.elements if e.role in ("group", "tabgroup", "toolbar") and e.title and e.depth <= 3
        ]

    @staticmethod
    def _context(e: Elem) -> str:
        for t in reversed(e.path):
            if t and t != e.title:
                return t
        return ""

    def _interactive(self, tree: Tree):
        """The elements a person could act on: named, enabled, not secure, pressable or editable."""
        out = []
        for e in tree.elements:
            if e.secure or not e.enabled:
                continue
            pressable = "AXPress" in e.actions or e.role in CLICK_ROLES
            named = bool(e.title or e.value) or e.role in ("checkbox", "radiobutton")
            if e.role in TEXT_ROLES or e.editable or (pressable and named):
                out.append(e)
        return out

    def _units(self, tree: Tree):
        """Text to copy from: every static text, each field as ``label: value``, each row or cell as one
        unit joining its descendants (the row ties a value to its subject), plus the user's selection."""
        units, seen = [], set()

        def add(t):
            t = " ".join(str(t or "").split())
            if 2 <= len(t) <= MAX_UNIT_LEN and t.lower() not in seen:
                seen.add(t.lower())
                units.append(t)

        if tree.selected_text:
            add(tree.selected_text)
        els = tree.elements
        for i, e in enumerate(els):
            if e.secure:
                continue
            if e.role in ("row", "cell", "outlinerow"):
                parts = []
                for d in els[i + 1 :]:
                    if d.depth <= e.depth:
                        break
                    if not d.secure and d.role not in SCROLL_ROLES:
                        parts.extend(p for p in (d.title, d.value) if p)
                if e.role != "cell" and parts:
                    add(" | ".join(dict.fromkeys(parts)))
            if e.role in TEXT_ONLY_ROLES:
                add(e.value or e.title)
            elif e.value and e.role not in CONTAINER_ROLES:
                add(f"{e.title}: {e.value}" if e.title else e.value)
            elif e.title and e.role not in CONTAINER_ROLES:
                add(e.title)
            if len(units) >= self._units_budget:
                break
        return units

    def _id(self, tree: Tree, e: Elem) -> str:
        """A planner-memory identity (role, title, ancestors, occurrence). Never used to act."""
        key = f"{e.role}|{e.title}|{'/'.join(e.path)}"
        n = sum(1 for o in tree.elements[: e.index] if f"{o.role}|{o.title}|{'/'.join(o.path)}" == key)
        return "e" + hashlib.sha1(f"{key}|{n}".encode()).hexdigest()[:7]

    def shrink_observation(self):
        self._ctx_budget = max(20, self._ctx_budget // 2)
        self._units_budget = max(40, self._units_budget // 2)

    # -- candidates --

    def candidates(self):
        tree = self._tree
        if tree is None:
            return []
        cands = self._interactive(tree)
        dupes = {}
        for e in cands:
            dupes[(e.role, e.title)] = dupes.get((e.role, e.title), 0) + 1
        out = []
        for e in cands:
            kind = "fill" if (e.role in TEXT_ROLES or e.editable) else "click"
            bits = [f'{e.role} "{e.title}"' if e.title else e.role]
            ctx = self._context(e)
            if ctx:
                bits.append(f"in: {ctx[: self._ctx_budget]}")
            if dupes[(e.role, e.title)] > 1 and e.frame:
                bits.append(_region(e.frame, tree))
            if e.value and e.role not in ("checkbox", "radiobutton"):
                bits.append(f"current value: {e.value}")
            if e.role in ("checkbox", "radiobutton"):
                bits.append("checked" if (e.selected or e.value in ("1", "true", "on")) else "unchecked")
            if e.selected and e.role in ("row", "tab", "cell", "outlinerow"):
                bits.append("selected")
            if e.expanded:
                bits.append("expanded")
            if e.focused:
                bits.append("focused")
            if e.role in ("popupbutton", "menubutton", "menubaritem"):
                bits.append("opens a menu; its items appear as choices next")
            side = "irreversible" if kind == "click" and IRREVERSIBLE_LABEL.search(e.title or "") else "unknown"
            out.append(
                {
                    "id": self._id(tree, e),
                    "idx": e.index,
                    "_token": tree.token,
                    "desc": " | ".join(bits),
                    "kind": kind,
                    "options": None,
                    "target_key": f"{e.role}:{e.title}",
                    "needs_commit": kind == "fill",
                    "side_effect": side,
                    "fam": None,
                    "value": e.value if not e.secure else "",
                }
            )
        scroll = next((e for e in tree.elements if e.role in SCROLL_ROLES and e.enabled), None)
        if scroll is not None:
            for d in ("down", "up"):
                out.append(
                    {
                        "id": f"scroll_{d}",
                        "idx": scroll.index,
                        "_token": tree.token,
                        "desc": f"scroll the window {d} to see more",
                        "kind": "scroll",
                        "scroll_dir": d,
                        "options": None,
                        "target_key": f"scroll:{d}",
                        "needs_commit": False,
                        "side_effect": "reversible",
                        "fam": "scroll",
                    }
                )
        return out

    # -- actions --

    def _elem(self, cand) -> Elem:
        tree = self._tree
        if tree is None or cand.get("_token") != tree.token:
            raise Stale("the window changed since this control was listed; look again before acting")
        for e in tree.elements:
            if e.index == cand["idx"]:
                return e
        raise Stale("the control is no longer in the window")

    def act(self, cand, value=None, kind=None):
        self.last_action_error = None
        self.last_action_note = None
        kind = kind or cand["kind"]
        try:
            e = self._elem(cand)
            tree = self._tree
            if kind == "scroll":
                self._after = self.driver.scroll(self.app, tree, e.index, cand.get("scroll_dir", "down"))
                return
            if kind in ("fill", "fill_enter"):
                if value is None:
                    raise DriverError("nothing to type: no value was chosen for this field")
                self._pending.append({"kind": "fill", "index": e.index, "key": _identity(e), "prev": e.value})
                after = self.driver.set_value(self.app, tree, e.index, str(value))
                now = _at(after, e.index)
                if now is not None and str(value) and str(value) not in (now.value or ""):
                    # The value did not read back: the field takes keystrokes rather than a set. Same
                    # drift check, same input policy, typed into the same element.
                    after = self.driver.type_text(self.app, after, e.index, str(value))
                    self.last_action_note = "the field did not accept a direct set; typed the value instead"
                if kind == "fill_enter":
                    after = self.driver.press_key(self.app, after, e.index, "return")
                self._after = after
                return
            if kind == "select":
                raise DriverError("a desktop popup is opened by pressing it; its items are then listed as choices")
            # click, and anything else that is a press
            self._pending.append({"kind": "press", "index": e.index, "key": _identity(e), "before": tree})
            self._after = self.driver.press(self.app, tree, e.index)
        except Stale as exc:
            self.last_action_error = f"stale: {exc}"[:160]
        except DriverError as exc:
            self.last_action_error = str(exc)[:160]

    def keyboard(self, cand=None, key=None, text=None, focus=False):
        """Type into the focused field, or focus ``cand`` first. Suggestions are the menu/list items the
        typing made appear (present after, absent before)."""
        empty = {"typed": "", "cursor": None, "sel_end": None, "options": []}
        self.last_action_error = None
        tree = self._tree
        if tree is None:
            return empty
        try:
            if focus:
                e = self._elem(cand)
                self._pending.append({"kind": "fill", "index": e.index, "key": _identity(e), "prev": e.value})
                self._focus_index = e.index
                if not e.focused:
                    tree = self.driver.press(self.app, tree, e.index)
                    self._tree = tree
            idx = self._focus_index
            if idx is None:
                f = next((x for x in tree.elements if x.focused), None)
                if f is None:
                    raise DriverError("no field is focused; focus one first")
                idx = f.index
            if key:
                after = self.driver.press_key(self.app, tree, idx, KEY_NAMES.get(key, key.lower()))
            elif text:
                after = self.driver.type_text(self.app, tree, idx, text)
            else:
                return empty
        except (Stale, DriverError) as exc:
            self.last_action_error = str(exc)[:160]
            return empty
        before_titles = {(x.role, x.title) for x in tree.elements}
        self._tree = after
        field = _at(after, idx)
        options = [
            x.title
            for x in after.elements
            if x.role in ("menuitem", "option", "row", "cell") and x.title and (x.role, x.title) not in before_titles
        ]
        return {"typed": field.value if field else "", "cursor": None, "sel_end": None, "options": options[:12]}

    def undo(self):
        """Restore the previous step's writes; report honestly when a press cannot be taken back."""
        frames, self._frames = self._frames, []
        if not frames:
            return {"restored": True, "error": None}
        try:
            tree = self.driver.snapshot(self.app)
            self._tree = tree
            for fr in reversed(frames):
                if fr["kind"] == "fill":
                    matches = [e for e in tree.elements if _identity(e) == fr["key"] and not e.secure]
                    if len(matches) != 1:
                        return {
                            "restored": False,
                            "error": f"cannot restore the field {fr['key'][1]!r}: {len(matches)} matching fields now",
                        }
                    if (matches[0].value or "") != (fr["prev"] or ""):
                        tree = self.driver.set_value(self.app, tree, matches[0].index, fr["prev"] or "")
                        self._tree = tree
                else:  # a press: only a menu it opened can be taken back
                    pressed = [e for e in tree.elements if _identity(e) == fr["key"]]
                    menus_now = any(e.role in ("menu", "menuitem") for e in tree.elements)
                    menus_before = any(e.role in ("menu", "menuitem") for e in fr["before"].elements)
                    if len(pressed) == 1 and (pressed[0].expanded or (menus_now and not menus_before)):
                        tree = self.driver.press_key(self.app, tree, pressed[0].index, "escape")
                        self._tree = tree
                    else:
                        return {"restored": False, "error": f"a press on {fr['key'][1]!r} cannot be taken back here"}
        except (Stale, DriverError) as exc:
            return {"restored": False, "error": str(exc)[:160]}
        self._after = self._tree
        return {"restored": True, "error": None}

    # -- misc protocol --

    def fingerprint(self, obs):
        form = json.dumps(
            [(e["role"], e["name"], e.get("value", ""), e.get("checked", "")) for e in obs["elements"]],
            ensure_ascii=False,
        )
        form += "|" + "|".join(obs.get("headings") or []) + "|" + obs.get("visible_text", "")
        return obs["url"] + "#" + hashlib.sha1(form.encode("utf-8")).hexdigest()[:10]

    def terminal(self, obs):
        """No code-owned completion check on a desktop: Jev's done signal and the register decide."""
        return False

    def acceptance(self, obs):
        return []

    def inflight(self):
        return 0  # the driver settles before returning a tree

    def wait_inflight(self, cap_ms=3000):
        return 0

    def screenshot(self, highlight_idx=None):
        return None  # Jev is text-only and the governed layer keeps pixels out of this path

    def close(self):
        with contextlib.suppress(Exception):
            self.driver.end()


def _identity(e: Elem):
    return (e.role, e.title, e.path)


def _at(tree: Tree, index: int):
    return next((e for e in tree.elements if e.index == index), None)


def _region(frame, tree: Tree) -> str:
    """Where a duplicate-labelled control sits, so two "Delete" buttons read differently."""
    x, y, w, h = frame
    xs = [f.frame[0] + f.frame[2] for f in tree.elements if f.frame]
    ys = [f.frame[1] + f.frame[3] for f in tree.elements if f.frame]
    width, height = (max(xs) if xs else 1) or 1, (max(ys) if ys else 1) or 1
    col = "left" if (x + w / 2) < width / 3 else "right" if (x + w / 2) > 2 * width / 3 else "center"
    row = "top" if (y + h / 2) < height / 3 else "bottom" if (y + h / 2) > 2 * height / 3 else "middle"
    return f"at the {row}-{col}"
