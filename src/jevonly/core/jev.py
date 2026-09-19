"""Minimal client for TypeSafe System One closed-choice judgments."""

import http.client
import json
import os
import time
from contextlib import suppress

API_HOST = "api.typesafe.ai"
API_PATH = "/v1/systemone"
MODEL = "jev-latest"
CALLS: list[dict] = []
ON_JEV = None
_CONN: http.client.HTTPSConnection | None = None


def _conn() -> http.client.HTTPSConnection:
    """Return the process-wide keep-alive HTTPS connection."""
    global _CONN
    if _CONN is None:
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


def jev(state, questions, tag):
    """Ask Jev to answer closed questions about state.

    The credential is read for every call so importing JevOnly never requires a key
    and long-running processes can rotate credentials without being restarted.
    """
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if not key:
        raise RuntimeError("TYPESAFE_API_KEY is required to call Jev")

    body = json.dumps({"state": state, "model": MODEL, "questions": questions}, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Connection": "keep-alive"}
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
