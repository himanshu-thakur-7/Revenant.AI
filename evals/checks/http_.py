"""HTTP-level checks for any returned URL (prototype or walkthrough).

check #1 in the design doc, and not by accident: this session found the
"tool reports success, URL is dead" bug class THREE separate times (a
tunnel-port mismatch, a stale-tunnel cache, and one plain deploy
failure). Every one of them would have been caught by url_alive() alone.
"""

from __future__ import annotations

import httpx

from evals.checks import Check

_HEADERS = {"ngrok-skip-browser-warning": "true"}
_INTERSTITIAL_MARKERS = (
    "ngrok-free.app", "You are about to visit", "Visit Site",
    "ERR_NGROK",
)


def url_alive(url: str, *, name: str = "url_alive", timeout: float = 20.0) -> Check:
    if not url:
        return Check(name, False, "no URL to check (empty)")
    if url.startswith("file:"):
        return Check(name, False, f"local-only URL, never left the machine: {url}")
    try:
        r = httpx.get(url, follow_redirects=True, timeout=timeout, headers=_HEADERS)
    except Exception as exc:  # noqa: BLE001
        return Check(name, False, f"request failed: {exc!r}", measured=None)
    return Check(name, r.status_code == 200, f"HTTP {r.status_code}", measured=r.status_code)


def content_type_is(url: str, expected_prefix: str, *, name: str = "content_type") -> Check:
    if not url or url.startswith("file:"):
        return Check(name, False, "no live URL to check")
    try:
        r = httpx.get(url, follow_redirects=True, timeout=15.0, headers=_HEADERS)
    except Exception as exc:  # noqa: BLE001
        return Check(name, False, f"request failed: {exc!r}")
    ct = r.headers.get("content-type", "")
    return Check(name, ct.startswith(expected_prefix), f"got '{ct}'", measured=ct)


def body_size_at_least(url: str, min_bytes: int, *, name: str = "body_size") -> Check:
    if not url or url.startswith("file:"):
        return Check(name, False, "no live URL to check")
    try:
        r = httpx.get(url, follow_redirects=True, timeout=15.0, headers=_HEADERS)
    except Exception as exc:  # noqa: BLE001
        return Check(name, False, f"request failed: {exc!r}")
    n = len(r.content)
    return Check(name, n >= min_bytes, f"{n} bytes (want >= {min_bytes})", measured=n)


def not_ngrok_interstitial(url: str, *, name: str = "not_ngrok_interstitial") -> Check:
    """The ngrok free-tier browser-warning page IS a 200 with real HTML —
    url_alive() alone would pass it. This is the check that specifically
    catches 'looks like a success, is actually ngrok's own warning page'
    (the same failure class PLAN.md already documented once)."""
    if not url or url.startswith("file:"):
        return Check(name, False, "no live URL to check")
    try:
        r = httpx.get(url, follow_redirects=True, timeout=15.0, headers=_HEADERS)
    except Exception as exc:  # noqa: BLE001
        return Check(name, False, f"request failed: {exc!r}")
    body = r.text[:4000]
    hit = next((m for m in _INTERSTITIAL_MARKERS if m in body), None)
    return Check(name, hit is None, f"found marker: {hit!r}" if hit else "clean", measured=hit)
