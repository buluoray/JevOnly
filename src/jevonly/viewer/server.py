"""Loopback-only live viewer for JevOnly runs."""

from __future__ import annotations

import argparse
import http.server
import json
import os
import socket
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
from contextlib import suppress
from importlib import resources
from pathlib import Path

import jevonly.core.jev as jev_module
from jevonly.core.loop import run_task

PRICE_PER_MTOK = 0.042
STATIC_CONTENT_TYPES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


class Run:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.events: list[tuple[int, str, dict]] = []
        self.seq = 0
        self.cond = threading.Condition(self.lock)
        self.running = False
        self.stop_flag = False
        self.started_at: float | None = None
        self.summary: dict | None = None
        self.tokens = 0
        self.calls = 0
        self.approval: dict | None = None  # the irreversible action waiting on the operator, if any
        self.approval_event = threading.Event()

    def reset(self) -> None:
        with self.lock:
            self.events.clear()
            self.seq = 0
            self.stop_flag = False
            self.summary = None
            self.tokens = 0
            self.calls = 0
            self.approval = None
            self.approval_event.clear()
            self.started_at = time.time()

    def push(self, kind: str, payload: dict) -> None:
        with self.cond:
            self.seq += 1
            self.events.append((self.seq, kind, {"t": time.time(), **payload}))
            self.cond.notify_all()

    def since(self, sequence: int) -> list[tuple[int, str, dict]]:
        with self.lock:
            return [event for event in self.events if event[0] > sequence]

    def wait(self, sequence: int, timeout: float) -> None:
        with self.cond:
            if not any(event[0] > sequence for event in self.events):
                self.cond.wait(timeout)


RUN = Run()


class Frames:
    """Keep only the newest screencast frame for live viewers."""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.latest: dict | None = None
        self.n = 0

    def set(self, frame: dict) -> None:
        with self.cond:
            self.latest = frame
            self.n += 1
            self.cond.notify_all()

    def forget(self) -> None:
        """Drop the last frame. A viewer that connects after Clear, or for a new run, used to be handed the
        previous run's final screen first and the stage flashed it before the new browser's first frame."""
        with self.cond:
            self.latest = None
            self.n += 1
            self.cond.notify_all()

    def newer_than(self, sequence: int, timeout: float) -> tuple[int, dict | None]:
        with self.cond:
            if self.n <= sequence:
                self.cond.wait(timeout)
            return (self.n, self.latest) if self.n > sequence else (sequence, None)


FRAMES = Frames()
FRAME_SOCK = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / f"jevonly-live-{os.getpid()}.sock"


def _serve_frames() -> None:
    if FRAME_SOCK.exists():
        FRAME_SOCK.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(FRAME_SOCK))
    os.chmod(FRAME_SOCK, 0o600)
    server.listen(2)
    previous = None
    while True:
        connection, _ = server.accept()
        # One browser child per run, and only the newest one paints. A run that was stopped can keep
        # its child alive for a moment (the quit is asynchronous), and two children writing into the
        # same frame slot flickered the stage between the old start page and the new one.
        if previous is not None:
            with suppress(OSError):
                previous.shutdown(socket.SHUT_RDWR)
            with suppress(OSError):
                previous.close()
        previous = connection
        threading.Thread(target=_read_frames, args=(connection,), daemon=True).start()


def _read_frames(connection: socket.socket) -> None:
    try:
        with connection, connection.makefile("rb") as lines:
            for line in lines:
                try:
                    FRAMES.set(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return  # the connection was cut because a newer run's child took over


def trim_state(state: dict) -> dict:
    """Trim long page text while preserving complete element lists."""

    def trim_observation(observation):
        if not isinstance(observation, dict) or "elements" not in observation:
            return observation
        trimmed = {key: value for key, value in observation.items() if key not in ("visible_text", "elements")}
        visible_text = observation.get("visible_text", "")
        trimmed["visible_text"] = visible_text[:1200] + (
            f" …[{len(visible_text) - 1200} more chars]" if len(visible_text) > 1200 else ""
        )
        trimmed["elements"] = observation["elements"]
        return trimmed

    return {
        key: trim_observation(value) if key in ("state", "state_before", "state_after") else value
        for key, value in state.items()
    }


def on_jev(tag: str, state: dict, questions: dict, answers: dict, meta: dict) -> None:
    RUN.tokens += meta.get("in_tok", 0)
    RUN.calls += 1
    RUN.push(
        "jev",
        {
            "tag": tag,
            "latency_s": meta.get("latency_s"),
            "in_tok": meta.get("in_tok"),
            "retries": meta.get("retries"),
            "questions": questions,
            "answers": answers,
            "state": trim_state(state),
            "tokens_total": RUN.tokens,
            "calls_total": RUN.calls,
            "cost_est_usd": round(RUN.tokens / 1e6 * PRICE_PER_MTOK, 5),
        },
    )


def on_event(kind: str, payload: dict) -> None:
    RUN.push(kind, payload)


def start_url_error(url: str) -> str | None:
    """The only check on a start URL: it must be an http(s) URL with a host. What the operator points the
    loop at is the operator's business; the page text will be sent to TypeSafe as part of every judgment."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https", "file"):
        return "URL must start with http://, https:// or file://"
    if parsed.scheme != "file" and not parsed.hostname:
        return "URL has no host"
    return None


def parse_facts(raw) -> dict[str, str]:
    """Parse facts from a mapping, JSON object, or key/value lines."""
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items()}
    text = (raw or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        return {str(key): str(value) for key, value in json.loads(text).items()}
    facts = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for separator in ("=", ":"):
            if separator in line:
                key, value = line.split(separator, 1)
                facts[key.strip()] = value.strip()
                break
        else:
            raise ValueError(f"fact line needs `key = value`: {line!r}")
    return facts


THRESHOLD_NAMES = ("verify", "offpath", "done", "risk")
APPROVAL_WAIT_S = 300


def parse_thresholds(raw) -> dict[str, float]:
    """The decision lines the form may move; anything missing or out of range keeps the loop's default."""
    out: dict[str, float] = {}
    for name in THRESHOLD_NAMES:
        try:
            value = float((raw or {}).get(name))
        except (TypeError, ValueError):
            continue
        if 0.0 <= value <= 1.0:
            out[name] = value
    return out


def ask_operator(info: dict) -> bool:
    """The `ask` policy in the viewer: park the run on this action, tell the page, and wait for the
    operator's answer (POST /approve). Stopping the run, or five minutes of silence, is a no."""
    with RUN.lock:
        RUN.approval = {**info, "decision": None}
        RUN.approval_event.clear()
    deadline = time.time() + APPROVAL_WAIT_S
    while time.time() < deadline and not RUN.stop_flag:
        if RUN.approval_event.wait(0.5):
            break
    with RUN.lock:
        decision = (RUN.approval or {}).get("decision")
        RUN.approval = None
    return decision == "allow"


def build_task(body: dict) -> tuple[dict, str]:
    goal = (body.get("goal") or "").strip()
    start = (body.get("start") or "").strip()
    if not goal:
        raise ValueError("goal is required")
    if not start:
        raise ValueError("target URL is required")
    if error := start_url_error(start):
        raise ValueError(error)
    facts = parse_facts(body.get("facts"))
    check = body.get("check") or {}
    terminal = {}
    if (check.get("text") or "").strip():
        terminal = {"text": check["text"].strip()}
    elif (check.get("url_contains") or "").strip():
        terminal = {"url_contains": check["url_contains"].strip()}
    max_steps = max(1, min(int(body.get("max_steps") or 25), 60))
    task = {
        "id": "live",
        "env": "browser",
        "start": start,
        "goal": goal,
        "facts": facts,
        "terminal": terminal,
        "faults": {},
        "max_steps": max_steps,
        "budget_s": max(60, min(int(body.get("budget_s") or 600), 1800)),
        "max_backtracks": 20,
        "max_cands": max(40, min(int(body.get("max_cands") or 200), 400)),
        "text_budget": 6000,
        "ctx_budget": 400,
        "prefer_main": bool(body.get("prefer_main")),
        "viewport_only": bool(body.get("viewport_only")),
        "headed": bool(body.get("headed")),
        "thresholds": parse_thresholds(body.get("thresholds")),
        "irreversible": body.get("irreversible") if body.get("irreversible") in ("refuse", "ask", "allow") else "ask",
        "prefilter": body.get("prefilter", True) is not False,
    }
    if task["irreversible"] == "ask":
        task["approve"] = ask_operator
    variant = ("std" if terminal else "noaccept") + ("_kb" if body.get("keyboard") else "")
    return task, variant


def run_thread(task: dict, variant: str, key: str) -> None:
    previous_key = os.environ.get("TYPESAFE_API_KEY")
    try:
        os.environ["TYPESAFE_API_KEY"] = key
        jev_module.ON_JEV = on_jev
        jev_module._reset_conn()
        jev_module.CALLS.clear()
        os.environ["JEV_FRAMES"] = str(FRAME_SOCK)
        os.environ["JEV_HEADED"] = "1" if task.get("headed") else ""
        log = run_task(task, variant, 0, on_event=on_event, stop=lambda: RUN.stop_flag)
        RUN.summary = {name: value for name, value in log.items() if name != "steps"}
    except Exception as exc:  # noqa: BLE001
        RUN.push("error", {"error": f"{type(exc).__name__}: {exc}"[:400], "traceback": traceback.format_exc()[-1200:]})
    finally:
        if previous_key is None:
            os.environ.pop("TYPESAFE_API_KEY", None)
        else:
            os.environ["TYPESAFE_API_KEY"] = previous_key
        jev_module.ON_JEV = None
        jev_module._reset_conn()
        with RUN.lock:
            RUN.running = False
        RUN.push(
            "closed",
            {
                "tokens_total": RUN.tokens,
                "calls_total": RUN.calls,
                "cost_est_usd": round(RUN.tokens / 1e6 * PRICE_PER_MTOK, 5),
            },
        )


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "JevOnlyLive/0.1"

    def log_message(self, format_string: str, *args) -> None:
        sys.stderr.write(f"{self.command} {urllib.parse.urlsplit(self.path).path}\n")

    def _json(self, code: int, value: dict) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _static(self, filename: str, content_type: str) -> None:
        data = resources.files("jevonly.viewer").joinpath("static", filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path in STATIC_CONTENT_TYPES:
            self._static(*STATIC_CONTENT_TYPES[path])
        elif path == "/status":
            self._json(
                200,
                {
                    "running": RUN.running,
                    "seq": RUN.seq,
                    "tokens": RUN.tokens,
                    "calls": RUN.calls,
                    "summary": RUN.summary,
                },
            )
        elif path == "/events":
            self._sse()
        elif path == "/frames":
            self._frames()
        else:
            self._json(404, {"error": "not found"})

    def _sse(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        last = int((query.get("since") or [self.headers.get("Last-Event-ID") or 0])[0])
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                batch = RUN.since(last)
                if not batch:
                    RUN.wait(last, 15.0)
                    batch = RUN.since(last)
                    if not batch:
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        continue
                for sequence, kind, payload in batch:
                    data = json.dumps({"seq": sequence, "kind": kind, **payload}, ensure_ascii=False)
                    self.wfile.write(f"id: {sequence}\nevent: {kind}\ndata: {data}\n\n".encode())
                    last = sequence
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _frames(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        sequence = 0
        try:
            while True:
                sequence, frame = FRAMES.newer_than(sequence, 15.0)
                if frame is None:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(("event: frame\ndata: " + json.dumps({"n": sequence, **frame}) + "\n\n").encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_POST(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"error": "body must be JSON"})
            return
        if path == "/run":
            key = (body.get("key") or "").strip()
            if not key:
                self._json(400, {"error": "API key is required"})
                return
            try:
                task, variant = build_task(body)
            except (ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
                return
            # A previous run may still be winding down: Stop only raises a flag the loop reads between
            # steps, and a step can sit in a page load for seconds. Run used to answer 409 in that
            # window with a one-line error, and the old run's frames (its old start URL) kept showing
            # -- which read as "the new URL was ignored". Ask it to stop and give it up to ten seconds.
            if RUN.running:
                RUN.stop_flag = True
                deadline = time.time() + 10
                while RUN.running and time.time() < deadline:
                    time.sleep(0.1)
            with RUN.lock:
                if RUN.running:
                    self._json(409, {"error": "the previous run is still stopping; try again in a few seconds"})
                    return
                RUN.running = True
            RUN.reset()
            FRAMES.forget()
            threading.Thread(target=run_thread, args=(task, variant, key), daemon=True).start()
            self._json(
                200,
                {
                    "ok": True,
                    "variant": variant,
                    "has_code_check": bool(task["terminal"]),
                    "facts": task["facts"],
                },
            )
        elif path == "/stop":
            RUN.stop_flag = True
            self._json(200, {"ok": True, "running": RUN.running})
        elif path == "/approve":
            decision = (body or {}).get("decision")
            if decision not in ("allow", "deny"):
                self._json(400, {"error": "decision must be allow or deny"})
                return
            with RUN.lock:
                if RUN.approval is None or RUN.approval.get("decision") is not None:
                    self._json(409, {"error": "nothing is waiting for approval"})
                    return
                RUN.approval["decision"] = decision
                RUN.approval_event.set()
            self._json(200, {"ok": True, "decision": decision})
        elif path == "/clear":
            # Forget the last run on the server too: its events carry page text, decision payloads and
            # screenshots, and /events would otherwise replay them into the next page load.
            with RUN.lock:
                if RUN.running:
                    self._json(409, {"error": "a run is in progress; stop it first"})
                    return
            RUN.reset()
            RUN.started_at = None
            FRAMES.forget()
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})


class QuietServer(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def create_server(port: int = 7791) -> QuietServer:
    server = QuietServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7791)
    args = parser.parse_args(argv)
    server = create_server(args.port)
    threading.Thread(target=_serve_frames, daemon=True).start()
    print(f"JevOnly Live on http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
