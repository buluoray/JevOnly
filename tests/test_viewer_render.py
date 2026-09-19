from __future__ import annotations

import os
import threading
from contextlib import contextmanager

import pytest

from jevonly.viewer.server import create_server

pytestmark = pytest.mark.e2e


@contextmanager
def running_server():
    server = create_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_realistic_events_render_timeline_and_collapsed_summaries():
    if os.environ.get("JEVONLY_E2E") != "1":
        pytest.skip("set JEVONLY_E2E=1 to run browser rendering tests")
    playwright = pytest.importorskip("playwright.sync_api")

    events = [
        [
            "start",
            {
                "goal": "Open Continue, then enter Paris as the destination",
                "start": "https://example.com/",
                "variant": "noaccept_kb",
                "has_code_check": False,
                "max_steps": 4,
                "viewport_only": False,
                "thresholds": {
                    "verify": 0.5,
                    "offpath": 0.6,
                    "done": 0.7,
                    "risk": 0.5,
                    "not_applied": 0.5,
                    "progress": 0.25,
                },
            },
        ],
        ["observe", {"step": 0, "url": "https://example.com/", "title": "Example", "n_elements": 2}],
        [
            "jev",
            {
                "tag": "next",
                "latency_s": 0.12,
                "questions": {
                    "target": {
                        "type": "choice",
                        "criteria": {"e1": "button Continue", "none": "nothing to do"},
                    }
                },
                "answers": {"target": {"choice": "e1", "probabilities": {"e1": 0.91, "none": 0.09}}},
                "state": {"task_goal": "Open Continue, then enter Paris as the destination"},
                "calls_total": 1,
                "tokens_total": 120,
                "cost_est_usd": 0.00001,
            },
        ],
        [
            "plan",
            {
                "step": 0,
                "n_candidates": 2,
                "ranked": [
                    {"id": "e1", "desc": 'button "Continue"', "p": 0.91},
                    {"id": "none", "desc": "nothing", "p": 0.09},
                ],
            },
        ],
        [
            "act",
            {
                "step": 0,
                "id": "e1",
                "desc": 'button "Continue"',
                "action_kind": "click",
                "side_effect": "reversible",
                "risk": 0.05,
            },
        ],
        [
            "verify",
            {
                "step": 0,
                "verify": 0.94,
                "progress": 0.88,
                "accepted": True,
                "undone": False,
                "outcome": "accepted",
            },
        ],
        ["observe", {"step": 1, "url": "https://example.com/form", "title": "Form", "n_elements": 1}],
        [
            "jev",
            {
                "tag": "value",
                "latency_s": 0.08,
                "questions": {
                    "value": {
                        "type": "choice",
                        "criteria": {"Paris": "Paris", "none": "nothing from the goal"},
                    }
                },
                "answers": {"value": {"choice": "Paris", "probabilities": {"Paris": 0.96, "none": 0.04}}},
                "state": {
                    "task_goal": "Open Continue, then enter Paris as the destination",
                    "field": 'textbox "Destination"',
                    "typed_so_far": "",
                },
                "calls_total": 2,
                "tokens_total": 240,
                "cost_est_usd": 0.00001,
            },
        ],
        [
            "plan",
            {
                "step": 1,
                "n_candidates": 1,
                "ranked": [{"id": "e2", "desc": 'textbox "Destination"', "p": 0.96}],
            },
        ],
        [
            "act",
            {
                "step": 1,
                "id": "e2",
                "desc": 'textbox "Destination" | typed "Paris"',
                "action_kind": "keyboard",
                "side_effect": "reversible",
                "risk": 0.02,
            },
        ],
        [
            "key",
            {
                "step": 1,
                "field": 'textbox "Destination"',
                "typed": "Paris|",
                "choice": "done",
                "p": 0.93,
                "suggestions": ["Paris, France"],
            },
        ],
        [
            "verify",
            {
                "step": 1,
                "verify": 0.9,
                "progress": 0.84,
                "accepted": True,
                "undone": False,
                "outcome": "accepted",
            },
        ],
    ]

    with running_server() as base_url, playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(base_url + "/?test=1")
            assert page.evaluate("() => !!window.__jevonly_test")
            page.evaluate(
                """events => {
                    for (const [kind, payload] of events) window.__jevonly_test.handlers[kind](payload);
                }""",
                events,
            )

            labels = page.locator(".timeline-placeholder, .who").all_text_contents()
            assert {"page", "Jev", "code"}.issubset(set(labels))
            summaries = page.locator("details.step > summary").all_text_contents()
            assert any('Pressed button "Continue" — succeeded' in text for text in summaries)
            assert any('Typed "Paris" into textbox "Destination" — succeeded' in text for text in summaries)
        finally:
            browser.close()
