"""Shared fixtures for the console UI suite.

Deliberately OUTSIDE pyproject.toml's `testpaths`, so `make test` /
`pytest -q` stays the fast offline T0 gate. These need a real browser and
a real HTTP server, so they run opt-in via `make test-ui`.

Everything here is still offline: a static file server plus Chromium. No
Hermes gateway, no LLM, no network egress — the console's own JS is the
system under test, and every network call it would make is stubbed at the
page level.
"""

from __future__ import annotations

import http.server
import socket
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBSITE_DIR = REPO_ROOT / "website"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def server() -> str:
    """Serve website/ on a random free port for the session.

    A random port (not a fixed 8790) so the suite can't collide with a dev
    server the developer already has running — that collision silently
    serves the WRONG build to the tests, which is exactly the kind of
    false green this suite exists to prevent.
    """
    port = _free_port()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(WEBSITE_DIR), **kw)

        def log_message(self, *a):   # keep pytest output clean
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


@pytest.fixture(scope="session")
def browser():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, server):
    """A console page with the invite gate already passed and every
    outbound API call stubbed, so tests exercise the UI's own logic
    rather than a live backend."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()

    # Stub the API surface. Default: healthy gateway, no runs.
    pg.route("**/api/health", lambda r: r.fulfill(
        status=200, content_type="application/json", body='{"ok":true}'))
    pg.route("**/api/auth", lambda r: r.fulfill(
        status=200, content_type="application/json", body='{"ok":true}'))

    pg.add_init_script("try{localStorage.setItem('revenant_gate_passed','1')}catch(e){}")
    pg.goto(f"{server}/console.html", wait_until="domcontentloaded")
    yield pg
    ctx.close()


@pytest.fixture
def js(page):
    """Evaluate an expression against the loaded console page."""
    def _run(expr):
        return page.evaluate(f"() => {{ {expr} }}")
    return _run
