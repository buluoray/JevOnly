"""Minimal client for TypeSafe System One closed-choice judgments."""

import http.client
import json
import os
import time
from contextlib import suppress

API_HOST = "api.typesafe.ai"
API_PATH = "/v1/systemone"
MODEL = os.environ.get("JEVONLY_MODEL", "jev-latest")
# JEVONLY_LOCAL=host:port sends every call to a System One server on this machine
# (plain HTTP, no key) instead of hosted Jev. Only a loopback host is accepted: the
# page state goes in the request, so this must never become a plain-text egress.
LOCAL = os.environ.get("JEVONLY_LOCAL", "")
_LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
if LOCAL and LOCAL.rpartition(":")[0] not in _LOOPBACK:
    raise RuntimeError(f"JEVONLY_LOCAL must name a loopback host, got {LOCAL!r}")
CALLS: list[dict] = []
ON_JEV = None
_CONN: http.client.HTTPConnection | None = None


def _conn() -> http.client.HTTPConnection:
    """Return the process-wide keep-alive connection."""
    global _CONN
    if _CONN is None:
        if LOCAL:
            host, _, port = LOCAL.rpartition(":")
            host = host.strip("[]")
            _CONN = http.client.HTTPConnection(host, int(port or 80), timeout=120)
        else:
            _CONN = http.client.HTTPSConnection(API_HOST, timeout=20)
    return _CONN


def _reset_conn() -> None:
    """Close the keep-alive connection, if one is open."""
    global _CONN
    try:
        if _CONN is not None:
            _CONN.close()
    finally:
        _CONN = None


def _trivial(q):
    """A choice with one option has one answer; a local server may refuse to be asked it."""
    crit = q.get("criteria") or {}
    if q.get("type") == "choice" and len(crit) == 1:
        only = next(iter(crit))
        return {"type": "choice", "choice": only, "probabilities": {only: 1.0}, "confidence": 1.0}
    return None


def jev(state, questions, tag):
    """Ask Jev to answer closed questions about state.

    The credential is read for every call so importing JevOnly never requires a key
    and long-running processes can rotate credentials without being restarted.
    """
    if LOCAL:
        fixed = {k: a for k, q in questions.items() if (a := _trivial(q)) is not None}
        if fixed:
            rest = {k: q for k, q in questions.items() if k not in fixed}
            return {**(_ask(state, rest, tag) if rest else {}), **fixed}
    return _ask(state, questions, tag)


def _ask(state, questions, tag):
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if not key and not LOCAL:
        raise RuntimeError("TYPESAFE_API_KEY is required to call Jev")

    body = json.dumps({"state": state, "model": MODEL, "questions": questions}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Connection": "keep-alive"}
    if key and not LOCAL:
        headers["Authorization"] = f"Bearer {key}"
    delay = 0.5
    last = None
    for attempt in range(6):
        started = time.monotonic()
        try:
            connection = _conn()
            connection.request("POST", API_PATH, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            if response.status in (429, 529) or response.status >= 500:
                last = f"HTTP {response.status}"
                time.sleep(delay)
                delay *= 2
                continue
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}: {raw.decode('utf-8', 'replace')[:200]}")
            data = json.loads(raw.decode("utf-8"))
            meta = {
                "tag": tag,
                "latency_s": round(time.monotonic() - started, 3),
                "retries": attempt,
                "in_tok": data.get("usage", {}).get("input_tokens", 0),
            }
            CALLS.append(meta)
            if ON_JEV is not None:
                with suppress(Exception):
                    ON_JEV(tag, state, questions, data["answers"], meta)
            return data["answers"]
        except (OSError, http.client.HTTPException) as exc:
            last = f"{type(exc).__name__}: {exc}"
            _reset_conn()
            time.sleep(delay)
            delay = min(delay * 2, 8)
    raise RuntimeError(f"Jev call failed after retries: {last}")
