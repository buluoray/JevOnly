#!/usr/bin/env python3
"""Serve JevOnly's offline HTML fixtures on a loopback-only HTTP server."""

from __future__ import annotations

import argparse
from contextlib import suppress
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class FixtureHandler(SimpleHTTPRequestHandler):
    """Serve fixtures without noisy request logs."""

    def log_message(self, format_string: str, *args: object) -> None:
        return


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Serve JevOnly's offline browser fixtures.")
    result.add_argument("--port", type=int, default=0, help="loopback port; 0 selects a free port")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not 0 <= args.port <= 65535:
        raise SystemExit("--port must be between 0 and 65535")

    handler = partial(FixtureHandler, directory=str(FIXTURES))
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        server.daemon_threads = True
        host, port = server.server_address[:2]
        base_url = f"http://{host}:{port}"
        print(base_url, flush=True)
        for fixture in sorted(FIXTURES.glob("*.html")):
            print(f"{base_url}/{fixture.name}", flush=True)
        with suppress(KeyboardInterrupt):
            server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
