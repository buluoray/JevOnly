"""Playwright browser bridge used by the JevOnly search loop."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from importlib import resources

from ...core.text import goal_spans

FIND_STOP = {
    "which",
    "there",
    "their",
    "these",
    "those",
    "where",
    "while",
    "after",
    "before",
    "until",
    "other",
    "about",
    "should",
    "would",
    "could",
    "between",
    "through",
    "using",
    "under",
    "again",
    "every",
    "first",
    "please",
    "click",
}
NODE = os.environ.get("JEVONLY_NODE") or shutil.which("node") or "node"


class BrowserEnv:
    name = "browser"

    def __init__(self, task):
        self.task = task
        env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
        self._server_resource = resources.as_file(
            resources.files("jevonly.envs.browser").joinpath("js/browser_server.js")
        )
        server = self._server_resource.__enter__()
        self.p = subprocess.Popen(
            [NODE, str(server)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
            encoding="utf-8",
        )
        self._url_stack = []
        # Observation budget. The caps below are generous on purpose: this is a stress test, and
        # "the model could not see it" must not be available as an explanation for a wrong answer.
        # Raising them only ADDS information. `prefer_main` is the one knob that reorders rather
        # than adds -- it demotes anything inside nav/header/footer, and on some sites the primary
        # control lives in a header -- so it stays per-task, on only where it was validated.
        self._snapopts = {
            "max_cands": task.get("max_cands", 120),
            "text_budget": task.get("text_budget", 6000),
            "prefer_main": bool(task.get("prefer_main", False)),
            "viewport_only": bool(
                task.get("viewport_only", False)
            ),  # see only what is on screen; scrolling is an action
            "ctx_budget": task.get("ctx_budget", 400),
        }
        self._finds = {}  # (url, text) -> how often `find` was used for that text on that page
        self._cmd(
            cmd="goto", url=task["start"], settle_ms=task.get("settle_ms"), settle_cap_ms=task.get("settle_cap_ms")
        )

    def _cmd(self, **kw):
        self.p.stdin.write(json.dumps(kw) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        if not line:
            raise RuntimeError("browser server died")
        r = json.loads(line)
        if not r.get("ok"):
            raise RuntimeError(r.get("error", "browser error"))
        return r

    def _ids(self):
        """Stable candidate ids: a hash of role|name|context (+ an occurrence counter for exact duplicates), so a
        rejected-action memo or a fact-usage record survives re-renders that shuffle DOM order."""
        seen, ids = {}, []
        for c in self._snap["candidates"]:
            key = f"{c['role']}|{c.get('name', '')}|{(c.get('ctx') or '')[:60]}"
            n = seen.get(key, 0)
            seen[key] = n + 1
            ids.append("e" + hashlib.sha1(f"{key}#{n}".encode()).hexdigest()[:6])
        return ids

    def shrink_observation(self):
        """Halve the snapshot budgets for the rest of the run: the last page state did not fit in one request."""
        o = self._snapopts
        o["max_cands"] = max(20, o["max_cands"] // 2)
        o["text_budget"] = max(500, o["text_budget"] // 2)
        o["ctx_budget"] = max(60, o["ctx_budget"] // 2)

    def observe(self):
        s = self._cmd(cmd="snapshot", **self._snapopts)["snapshot"]
        self._snap = s
        ids = self._ids()
        dialogs = s.get("dialogs") or []
        if dialogs:
            self._last_msgs = dialogs
        elif s["url"].split("#", 1)[0] != getattr(self, "_last_msgs_url", s["url"].split("#", 1)[0]):
            self._last_msgs = []
        self._last_msgs_url = s["url"].split("#", 1)[0]
        obs = {
            "url": s["url"],
            "title": s["title"],
            "headings": s["headings"],
            "visible_text": s["visible_text"][: self._snapopts["text_budget"]],
            "elements": [
                {
                    "id": ids[i],
                    "role": c["role"],
                    "name": c.get("name", ""),
                    **({"context": c["ctx"][: self._snapopts["ctx_budget"]]} if c.get("ctx") else {}),
                    **({"value": c["value"]} if c.get("value") else {}),
                    **({"hint": c["hint"]} if c.get("hint") else {}),
                    **({"checked": c["checked"]} if "checked" in c else {}),
                    **({"expanded": c["expanded"]} if "expanded" in c else {}),
                    **({"options": c["options"][:8]} if c.get("options") else {}),
                }
                for i, c in enumerate(s["candidates"])
            ],
        }
        if dialogs:
            obs["popup_messages_from_last_action"] = dialogs
        elif getattr(self, "_last_msgs", None):
            obs["last_popup_message_on_this_page"] = self._last_msgs[-1]
        if s.get("modal"):
            obs["open_dialog"] = (
                f"a modal dialog is open ({s['modal']}); only its controls are listed, the page behind it is inert"
            )
        if s.get("truncated_runs"):
            # The cap fell inside a repeated widget. Say so, or the model reads the listed members as all
            # there is (a calendar that ends in November, a list of 20 when there are 700).
            obs["elements_not_listed"] = "; ".join(
                f'{r["total"] - r["shown"]} more {r["role"]}s like "{r["first"]}" … "{r["last"]}" (a repeated grid/list; {r["shown"]} of {r["total"]} listed)'
                for r in s["truncated_runs"]
            )
        return obs

    def candidates(self):
        out = []
        ids = self._ids()
        for i, c in enumerate(self._snap["candidates"]):
            bits = [f'{c["role"]} "{c.get("name", "")}"']
            if c.get("ctx") and c["ctx"] != c.get("name"):
                bits.append(f"in: {c['ctx'][: self._snapopts['ctx_budget']]}")
            if c.get("options"):
                bits.append("options: " + ", ".join(c["options"][:8]))
            if "checked" in c:
                bits.append("checked" if c["checked"] else "unchecked")
            if c.get("value"):
                bits.append(f"current value: {c['value']}")
            elif c.get("placeholder"):
                bits.append(f"placeholder: {c['placeholder']}")
            if c.get("hint"):
                bits.append(f"hint: {c['hint']}")
            if self._snap.get("modal"):
                # Everything listed is inside the modal (the page behind it is not offered). Saying so
                # on each control tells verify that "Done." closing the dialog IS the button's job: with
                # the label alone ("Done. Search for one-way flights…") the judgment sat at 0.42-0.54.
                bits.append("in the open dialog")
            r = c["role"]
            tag = c.get("tag")
            if r == "scroll":
                # Viewport mode: a pseudo-candidate the browser child adds when the page continues off
                # screen. Not a DOM element; act() sends the direction instead of an index.
                kind = "scroll"
            elif r == "combobox":
                # role=combobox covers three different controls. Only a real <select> takes selectOption;
                # an ARIA combobox on an <input> (Google Flights "Where to?") is typed into and commits on
                # Enter or by picking a suggestion; a combobox on a div opens a list on click. Treating them
                # all as <select> failed with "Element is not a <select> element" and stalled the task.
                kind = "select" if tag == "select" else "fill" if tag in ("input", "textarea") else "click"
                if kind == "fill":
                    bits.append("type to search, suggestions appear")
            else:
                kind = (
                    "click"
                    if r
                    in (
                        "button",
                        "link",
                        "checkbox",
                        "radio",
                        "tab",
                        "menuitem",
                        "menuitemcheckbox",
                        "menuitemradio",
                        "option",
                        "switch",
                        "treeitem",
                    )
                    else "fill_enter"
                    if r == "searchbox"
                    else "fill"
                )
            # Side effect class: typing/selecting is reversible form state; a link is a navigation; a button/checkbox
            # may commit something -- the environment does not know, so the loop asks Jev (Q_RISK) once per description.
            side = "reversible" if kind in ("fill", "select", "scroll") else "navigation" if r == "link" else "unknown"
            out.append(
                {
                    "id": ids[i],
                    "idx": i,
                    "desc": " | ".join(bits),
                    "kind": kind,
                    "options": c.get("options"),
                    "target_key": f"{r}:{c.get('name', '')}",
                    "needs_commit": kind == "fill",
                    "side_effect": side,
                    "fam": c.get("fam"),
                    **({"scroll_dir": c["pseudo"]} if c.get("pseudo") else {}),
                    **({"host": c["host"]} if c.get("host") else {}),
                }
            )
        if self._snap.get("text_units") or self._snap.get("visible_text"):
            # The loop's register: copy a value shown on this page (a price, a name, a code) so it can be
            # typed into a field later or reported as the answer. Not a DOM element; the loop handles it.
            out.append(
                {
                    "id": "copy",
                    "idx": -1,
                    "kind": "copy",
                    "options": None,
                    "target_key": "copy",
                    "needs_commit": False,
                    "side_effect": "reversible",
                    "fam": "copy",
                    "desc": "copy a value shown on this page -- text a later step needs, to type into a field or to report as the result; changes nothing on the page",
                }
            )
        if any(c.get("role") == "scroll" for c in self._snap["candidates"]):
            # Ctrl+F: the page continues off screen, so offer scrolling straight to a text -- a piece of the
            # goal or a copied value -- instead of paging. The option-binding question picks which text.
            goal = self.task.get("goal", "")
            texts = goal_spans(goal) + [w for w in re.findall(r"[A-Za-z][a-z]{4,}", goal) if w.lower() not in FIND_STOP]
            texts += [str(v) for v in self.task.get("facts", {}).values() if isinstance(v, str) and 2 <= len(v) <= 60]
            title = (self._snap.get("title") or "").lower()
            url = self._snap["url"].split("#", 1)[0]
            # The page's own subject is on every screen of it (finding "Seattle" on the Seattle article
            # is paging with a different name), and the same text is found at most twice per page.
            texts = list(
                dict.fromkeys(
                    t for t in texts if t and t.lower() not in title and self._finds.get((url, t.lower()), 0) < 2
                )
            )
            if texts:
                out.append(
                    {
                        "id": "find",
                        "idx": -1,
                        "kind": "find",
                        "options": texts[:12],
                        "target_key": "find",
                        "needs_commit": False,
                        "side_effect": "reversible",
                        "fam": "scroll",
                        "desc": "find a text on this page and scroll to it (a piece of the goal or a copied value), like Ctrl+F",
                    }
                )
        return out

    @staticmethod
    def _field_keys(cands):
        """Identity of each text-ish field: role|name|context plus an occurrence counter for duplicates.
        Form state is keyed by this, never by list index: indices shift whenever a dialog opens or closes,
        and an index-keyed restore after a date picker closed wrote the date into the wrong box."""
        seen, keys = {}, []
        for c in cands:
            if c["role"] in ("textbox", "searchbox", "combobox"):
                k = f"{c['role']}|{c.get('name', '')}|{(c.get('ctx') or '')[:60]}"
                n = seen.get(k, 0)
                seen[k] = n + 1
                keys.append(f"{k}#{n}")
            else:
                keys.append(None)
        return keys

    def _form_state(self):
        keys = self._field_keys(self._snap["candidates"])
        return {k: c.get("value", "") for k, c in zip(keys, self._snap["candidates"], strict=True) if k is not None}

    def act(self, cand, value=None, kind=None):
        self._url_stack.append((self._snap["url"], self._form_state()))
        if (kind or cand["kind"]) == "scroll":
            value = cand.get("scroll_dir", "down")
        try:
            r = self._cmd(
                cmd="act",
                index=cand["idx"],
                action=kind or cand["kind"],
                value=value,
                act_settle_ms=self.task.get("act_settle_ms"),
                settle_cap_ms=self.task.get("settle_cap_ms"),
            )
            # How long the child waited for the page to stop changing before the observation (the
            # stability wait in browser_server.js), and whether it did stop before the cap.
            self.last_settled = (
                {"settled_ms": r.get("settled_ms"), "settled": r.get("settled")} if "settled_ms" in r else None
            )
            if r.get("found"):
                self.last_action_note = f"scrolled to the first occurrence of {str(value)[:60]!r} not already on screen"
                k = (self._snap["url"].split("#", 1)[0], str(value).lower())
                self._finds[k] = self._finds.get(k, 0) + 1
            elif (kind or cand.get("kind")) == "find" and value is not None:
                # not on this page: never offer it here again (it was retried across two plans on Portland)
                k = (self._snap["url"].split("#", 1)[0], str(value).lower())
                self._finds[k] = 2
            if r.get("recovered"):
                self.last_action_note = (
                    f"the target was covered by an open popover; dismissed it ({r['recovered']}) and clicked again"
                )
        except RuntimeError as exc:
            if "browser server died" in str(exc):
                raise
            # A detached/re-rendered/obscured element is an action that did nothing:
            # the loop's verify + no-effect path decides what to do next.
            self.last_action_error = str(exc)[:160]

    def inflight(self):
        """How many requests started by the last action are still in flight (0 when the page has answered)."""
        try:
            return int(self._cmd(cmd="inflight").get("pending", 0))
        except RuntimeError as exc:
            if "browser server died" in str(exc):
                raise
            return 0

    def wait_inflight(self, cap_ms=3000):
        """Wait, up to cap_ms, while requests started by the last action are still in flight. Returns the
        milliseconds waited. Actions themselves never wait: the loop observes at once and calls this only
        when a verification failed while the page was still loading, or before an irreversible action."""
        try:
            r = self._cmd(cmd="wait_inflight", cap_ms=cap_ms)
        except RuntimeError as exc:
            if "browser server died" in str(exc):
                raise
            return 0
        self.last_settled = {"settled_ms": r.get("settled_ms"), "settled": r.get("settled")}
        return int(r.get("settled_ms") or 0)

    def partial(self, cand, value=None, kind=None):
        """Fault: the action half-lands -- the field takes only the first part of the value, the way a
        maxlength-capped input does. Something really changed, but not to what the goal asked for, so
        this one has to read as a failure. Returns False when the action carries no value to halve."""
        if value is None or (kind or cand["kind"]) not in ("fill", "fill_enter"):
            return False
        self._url_stack.append((self._snap["url"], self._form_state()))
        try:
            self._cmd(
                cmd="truncate_fill",
                index=cand["idx"],
                action=kind or cand["kind"],
                value=value,
                act_settle_ms=self.task.get("act_settle_ms"),
                settle_cap_ms=self.task.get("settle_cap_ms"),
            )
        except RuntimeError as exc:
            if "browser server died" in str(exc):
                raise
            self.last_action_error = str(exc)[:160]
        return True

    def popup(self, cand, value=None, kind=None):
        """Fault: the action succeeds normally, then an unrelated overlay appears over the page with
        controls of its own. Nothing about the goal changed, so reading this as a failure is the error
        being tested for."""
        self.act(cand, value, kind)
        self._cmd(cmd="inject_popup")
        return True

    def noop(self):
        self._cmd(cmd="noop")

    def detour(self, fault):
        idx = next(
            (
                i
                for i, c in enumerate(self._snap["candidates"])
                if fault["link"].lower() in (c.get("name") or "").lower()
            ),
            None,
        )
        if idx is None:
            return False
        self._url_stack.append((self._snap["url"], self._form_state()))
        self._cmd(cmd="act", index=idx, action="click")
        return True

    def undo(self):
        """Undo the last action. Same-page change (a fill or select): restore the previous field values in
        place -- reloading would wipe every field. Navigation: fast back (5 s cap), then a direct goto of
        the URL we came from if the page did not move (POST result pages often refuse to go back quickly)."""
        if not self._url_stack:
            self._cmd(cmd="back")
            return
        prev_url, prev_form = self._url_stack.pop()
        cur = self._cmd(cmd="snapshot", **self._snapopts)["snapshot"]
        if cur["url"] == prev_url:
            keys = self._field_keys(cur["candidates"])
            for i, (k, c) in enumerate(zip(keys, cur["candidates"], strict=True)):
                if k is None or k not in prev_form:
                    continue
                if c["role"] in ("textbox", "searchbox") and (c.get("value") or "") != prev_form[k]:
                    self._cmd(cmd="act", index=i, action="fill", value=prev_form[k])
                elif c["role"] == "combobox" and prev_form[k] and (c.get("value") or "") != prev_form[k]:
                    self._cmd(
                        cmd="act",
                        index=i,
                        action=("select" if c.get("tag") == "select" else "fill"),
                        value=prev_form[k],
                    )
            return
        r = self._cmd(cmd="back")
        if r.get("url") != prev_url:
            self._cmd(cmd="goto", url=prev_url)

    def fingerprint(self, obs):
        form = json.dumps(
            [(e["role"], e["name"], e.get("value", ""), e.get("checked", "")) for e in obs["elements"]],
            ensure_ascii=False,
        )
        form += "|" + "|".join(obs.get("popup_messages_from_last_action") or [])
        form += "|" + "|".join(obs.get("headings") or []) + "|" + obs.get("visible_text", "")
        return obs["url"].split("#", 1)[0] + "#" + hashlib.sha1(form.encode("utf-8")).hexdigest()[:10]

    def _priced_rows(self):
        """Result rows the model was actually shown, as (price, expanded). Prices are read from the
        snapshot each time, so the check survives fares changing between runs."""
        rows = []
        for c in self._snap["candidates"]:
            if not (c.get("name") or "").startswith("Flight details"):
                continue
            m = re.search(r"\$(\d[\d,]*)", (c.get("ctx") or "") + " " + (c.get("name") or ""))
            if m:
                rows.append((int(m.group(1).replace(",", "")), bool(c.get("expanded"))))
        return rows

    def _filter_applied(self, spec):
        """Whether a filter is applied, read off the filter button's own name. Measured shapes:
        unset it reads "Stops, Not selected"; with nonstop chosen it reads "Nonstop, Stops, Selected"
        (the head word moves, so matching cannot assume a prefix), and a sibling "Clear Stops" button
        also carries the head word but never the "Selected" marker. `spec` is "Head: wanted value".
        """
        head = spec.split(":", 1)[0].strip().lower()
        want = spec.split(":", 1)[1].strip().lower() if ":" in spec else ""
        for c in self._snap["candidates"]:
            n = (c.get("name") or "").lower()
            if head in n and "selected" in n and "not selected" not in n:
                return want in n if want else True
        return False

    def terminal(self, obs):
        t = self.task["terminal"]
        s = self._snap
        text = (
            s["visible_text"]
            + " | "
            + " | ".join(s.get("headings", []))
            + " | "
            + " | ".join((c.get("name") or "") for c in s["candidates"])
        )
        if t.get("cheapest_details_open"):
            rows = self._priced_rows()
            if len(rows) < 2:
                return False
            if t.get("requires_filter") and not self._filter_applied(t["requires_filter"]):
                return False
            cheapest = min(p for p, _ in rows)
            return any(exp for p, exp in rows if p == cheapest)
        if "url_contains" in t:
            return t["url_contains"] in s["url"]
        if "text" in t:
            return t["text"] in text
        if "completed_text" in t:
            checked = [
                c
                for c in s["candidates"]
                if c["role"] == "checkbox" and c.get("checked") and t["completed_text"] in (c.get("ctx") or "")
            ]
            items = [c for c in s["candidates"] if c["role"] == "checkbox" and c.get("name") == "Toggle Todo"]
            return bool(checked) and len(items) == t["count_items"]
        return False

    def acceptance(self, obs):
        t = self.task["terminal"]
        s = self._snap
        if t.get("cheapest_details_open"):
            rows = self._priced_rows()
            if len(rows) < 2:
                return ["flight results: not loaded yet (fewer than two priced rows are visible)"]
            cheapest = min(p for p, _ in rows)
            opened = [p for p, exp in rows if exp]
            lines = []
            if t.get("requires_filter"):
                lines.append(
                    f'required filter "{t["requires_filter"]}": '
                    + ("applied" if self._filter_applied(t["requires_filter"]) else "not applied yet")
                )
            return lines + [
                f"cheapest visible fare: ${cheapest} (of {len(rows)} priced rows)",
                (
                    "flight details panel: open for $" + str(opened[0])
                    if opened
                    else "flight details panel: not open for any row"
                )
                + ("" if opened and opened[0] == cheapest else " — the goal asks for the cheapest one"),
            ]
        if "url_contains" in t:
            return [
                f'expected to reach a page whose address contains "{t["url_contains"]}": {"reached" if t["url_contains"] in s["url"] else "not yet"}'
            ]
        if "text" in t:
            seen = t["text"] in (s["visible_text"] + " ".join(s.get("headings", [])))
            return [f'expected confirmation text "{t["text"]}": {"visible" if seen else "not visible yet"}']
        if "completed_text" in t:
            items = [c for c in s["candidates"] if c["role"] == "checkbox" and c.get("name") == "Toggle Todo"]
            checked = [c for c in items if c.get("checked")]
            return [
                f"todo items present: {len(items)} (expected {t['count_items']})",
                f'items marked completed: {[c.get("ctx") for c in checked] or "none"} (expected: "{t["completed_text"]}")',
            ]
        return []

    def close(self):
        try:
            self._cmd(cmd="quit")
        except (OSError, RuntimeError, ValueError):
            return
        finally:
            self._server_resource.__exit__(None, None, None)

    def open_dialog(self):
        """Peek: is a modal dialog open right now, and what clickable choices does it hold? A fresh snapshot
        that is NOT stored as the step's observation. Returns None or {"label", "choices": [names...]}."""
        try:
            s = self._cmd(cmd="snapshot", **self._snapopts)["snapshot"]
        except Exception:  # noqa: BLE001
            return None
        if not s.get("modal"):
            return None
        # Buttons and grid cells are a picker's choices. `option`s are an autocomplete's suggestions and
        # mean the opposite: keep typing. (Google's destination overlay is a modal with 8 options and one
        # button; counting the options made the loop back out of typing there and lose a step.)
        choices = [c.get("name", "") for c in s["candidates"] if c["role"] in ("button", "gridcell") and c.get("name")]
        return {"label": s["modal"], "choices": choices}

    def keyboard(self, cand=None, key=None, text=None, focus=False):
        """Type one key (or a short text) into the focused field. `focus=True` first clicks `cand` and records
        the pre-typing state for undo. Returns {"typed": <field text>, "options": [visible suggestions]}."""
        if focus:
            self._url_stack.append((self._snap["url"], self._form_state()))
        kw = {
            "cmd": "keyboard",
            "focus": bool(focus),
            "settle_ms": self.task.get("act_settle_ms"),
            "settle_cap_ms": self.task.get("settle_cap_ms"),
        }
        if focus and cand is not None:
            kw["index"] = cand["idx"]
        if key:
            kw["key"] = key
        elif text:
            kw["text"] = text
        try:
            r = self._cmd(**kw)
        except RuntimeError as exc:
            if "browser server died" in str(exc):
                raise
            self.last_action_error = str(exc)[:160]
            return {"typed": "", "cursor": None, "sel_end": None, "options": []}
        return {
            "typed": r.get("typed", ""),
            "cursor": r.get("cursor"),
            "sel_end": r.get("sel_end"),
            "options": r.get("options", []),
        }

    def screenshot(self, highlight_idx=None):
        """Viewport JPEG (base64) for a live viewer; `highlight_idx` outlines that candidate. The outline
        stays on until the next act/truncate_fill clears it, so a live screencast shows the target being
        used; the model's observation is unaffected (snapshots read roles/names/values, never styles).
        Returns None on failure -- a viewer must never break the run."""
        try:
            kw = {"cmd": "screenshot"}
            if highlight_idx is not None:
                kw["highlight"] = int(highlight_idx)
                kw["keep"] = True
            return self._cmd(**kw).get("jpeg_b64")
        except Exception:  # noqa: BLE001
            return None
