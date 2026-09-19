from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager

from jevonly.viewer.server import create_server


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


def get(base_url: str, path: str):
    return urllib.request.urlopen(base_url + path, timeout=2)


def post(base_url: str, path: str, body: dict):
    request = urllib.request.Request(
        base_url + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return urllib.request.urlopen(request, timeout=2)


def test_static_assets_and_status_have_expected_content_types():
    expected = {
        "/": "text/html",
        "/app.js": "text/javascript",
        "/style.css": "text/css",
        "/status": "application/json",
    }
    with running_server() as base_url:
        for path, content_type in expected.items():
            with get(base_url, path) as response:
                assert response.status == 200
                assert response.headers.get_content_type() == content_type
                assert response.read()


def test_run_refuses_a_start_url_without_a_scheme():
    with running_server() as base_url:
        try:
            post(
                base_url,
                "/run",
                {"key": "tsk_your_key_here", "goal": "Read the page", "start": "example.com/page"},
            )
        except urllib.error.HTTPError as error:
            assert error.code == 400
            assert "URL must start with" in json.loads(error.read())["error"]
        else:
            raise AssertionError("a start URL without a scheme was accepted")


def test_static_path_traversal_is_refused():
    with running_server() as base_url:
        try:
            get(base_url, "/%2e%2e/server.py")
        except urllib.error.HTTPError as error:
            assert error.code == 404
            assert json.loads(error.read()) == {"error": "not found"}
        else:
            raise AssertionError("path traversal was accepted")


def test_clear_forgets_the_last_run_on_the_server():
    with running_server() as base_url:
        post(base_url, "/clear", {})
        status = json.loads(urllib.request.urlopen(f"{base_url}/status", timeout=5).read())
        assert status["running"] is False


def test_approve_with_nothing_pending_is_refused():
    with running_server() as base_url:
        try:
            post(base_url, "/approve", {"decision": "allow"})
        except urllib.error.HTTPError as error:
            assert error.code == 409
        else:
            raise AssertionError("an approval with nothing pending was accepted")
