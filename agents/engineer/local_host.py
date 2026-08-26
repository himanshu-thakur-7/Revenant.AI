"""Serve built prototypes locally and expose them over a single ngrok tunnel.

Foolproof hosting: no Cloudflare deploy step, no quota, no build wait. The MCP
server process is long-lived, so we start ONE local HTTP server + ONE ngrok
tunnel lazily and reuse them for every prototype — each served under its own
``/<slug>/`` path. ``publish(slug, html)`` writes the file and returns the live
public URL.

Requires ngrok installed + an authtoken configured
(`ngrok config add-authtoken <token>`).
"""

from __future__ import annotations

import http.server
import socket
import socketserver
import subprocess
import threading
import time
from pathlib import Path

import httpx

PROTO_ROOT = Path.home() / ".revenant" / "prototypes"
_NGROK_API = "http://127.0.0.1:4040/api/tunnels"

_lock = threading.Lock()
_server: socketserver.TCPServer | None = None
_port: int | None = None
_public_url: str = ""
_ngrok_proc: subprocess.Popen | None = None


def available() -> bool:
    """True if ngrok is installed (an authtoken is still required to tunnel)."""
    import shutil
    return shutil.which("ngrok") is not None


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _ensure_server() -> int:
    global _server, _port
    if _server is not None:
        return _port  # type: ignore[return-value]
    PROTO_ROOT.mkdir(parents=True, exist_ok=True)
    _port = _free_port()
    root = str(PROTO_ROOT)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=root, **k)

        def log_message(self, *a):  # silence
            pass

        def end_headers(self):
            # No-cache so a re-published (polished) prototype shows immediately.
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", _port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _server = srv
    return _port


def _existing_tunnel(port: int) -> str:
    """Return an already-running ngrok https tunnel that forwards to
    ``port``, or '' if none.

    ngrok's local API lists EVERY tunnel active on the machine, not just
    ones this process started — e.g. a tunnel someone opened by hand to a
    completely different local service. The old version took the first
    https tunnel it saw with no port check, so whenever any other ngrok
    tunnel was already running, every prototype/walkthrough URL silently
    pointed at the wrong backend (404, or worse, someone else's server)
    while build_prototype / film_walkthrough still reported success —
    the artifact never actually existed at the URL handed back.
    """
    try:
        r = httpx.get(_NGROK_API, timeout=3)
        want = {f"http://localhost:{port}", f"http://127.0.0.1:{port}"}
        for t in r.json().get("tunnels", []):
            u = t.get("public_url", "")
            addr = (t.get("config") or {}).get("addr", "")
            if u.startswith("https") and addr in want:
                return u
    except Exception:
        pass
    return ""


def _ensure_tunnel(port: int) -> str:
    global _public_url, _ngrok_proc
    if _public_url:
        # Cached from an earlier call in this same process — but ngrok can
        # die independently (crash, network blip, laptop sleep/wake, or
        # someone kills it by hand) without this process knowing. Reusing a
        # dead cached URL blindly is exactly how this bug hid the longest:
        # every future publish() in the process's lifetime would keep
        # returning a URL that used to work. Re-validate against the CURRENT
        # tunnel list every time; only trust the cache if it's still there.
        if _public_url in {t for t in [_existing_tunnel(port)] if t}:
            return _public_url
        _public_url = ""
        _ngrok_proc = None
    # Reuse a tunnel that's already up FOR OUR OWN PORT (e.g. left by a
    # prior process) — never someone else's tunnel to a different service.
    existing = _existing_tunnel(port)
    if existing:
        _public_url = existing
        return _public_url
    _ngrok_proc = subprocess.Popen(
        ["ngrok", "http", str(port), "--log=stdout"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        u = _existing_tunnel(port)
        if u:
            _public_url = u
            return _public_url
        time.sleep(0.5)
    raise RuntimeError("ngrok tunnel did not come up (is the authtoken set?)")


def publish(slug: str, html: str) -> str:
    """Write ``html`` under ``/<slug>/`` and return its live public URL."""
    with _lock:
        port = _ensure_server()
        public = _ensure_tunnel(port)
    d = PROTO_ROOT / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(html, encoding="utf-8")
    return f"{public.rstrip('/')}/{slug}/"
