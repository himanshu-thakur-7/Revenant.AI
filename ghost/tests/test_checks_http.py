"""evals/checks/http_.py — the checks that decide whether a shipped
artifact is actually reachable.

These are the hard gates. This session found the "tool reported success,
URL is dead" bug class three separate times, and url_alive() alone would
have caught every one — so the checks themselves have to be correct, and
in particular they must FAIL CLOSED: any uncertainty (timeout, refused
connection, unparseable response) has to read as "not proven alive",
never as a pass.

Offline: httpx.get is stubbed, so no network is touched.
"""

from __future__ import annotations

import httpx
import pytest

import evals.checks.http_ as http_


class _Resp:
    def __init__(self, status=200, text="", content=b"", ctype="text/html"):
        self.status_code = status
        self.text = text
        self.content = content or text.encode()
        self.headers = {"content-type": ctype}


@pytest.fixture
def stub(monkeypatch):
    """Install a fake httpx.get. Pass a _Resp or an Exception to raise."""
    def _install(result):
        def fake_get(url, **kw):
            if isinstance(result, Exception):
                raise result
            return result
        monkeypatch.setattr(http_.httpx, "get", fake_get)
    return _install


# ── url_alive ─────────────────────────────────────────────────────────

def test_a_200_is_alive(stub):
    stub(_Resp(200))
    assert http_.url_alive("https://x.test/").passed


@pytest.mark.parametrize("status", [301, 400, 401, 403, 404, 410, 500, 502, 503])
def test_any_non_200_is_not_alive(stub, status):
    # follow_redirects=True means a 3xx reaching us is a redirect that did
    # not resolve — still not a working page.
    stub(_Resp(status))
    c = http_.url_alive("https://x.test/")
    assert not c.passed
    assert str(status) in c.detail


def test_an_empty_url_fails_rather_than_requesting(stub):
    stub(_Resp(200))
    assert not http_.url_alive("").passed


def test_a_file_url_is_never_considered_alive(stub):
    # A file:// URL means the deploy never left the machine — a prospect
    # could never open it. This is a real failure, not a skip.
    stub(_Resp(200))
    c = http_.url_alive("file:///tmp/index.html")
    assert not c.passed
    assert "never left the machine" in c.detail


@pytest.mark.parametrize("exc", [
    httpx.ConnectError("refused"),
    httpx.ReadTimeout("timed out"),
    httpx.ConnectTimeout("timed out"),
    Exception("something unexpected"),
])
def test_network_failures_fail_closed_rather_than_raising(stub, exc):
    # Fail-closed matters: an exception escaping here would abort the whole
    # eval run, and a pass would ship a dead link to a prospect.
    stub(exc)
    c = http_.url_alive("https://x.test/")
    assert not c.passed
    assert "request failed" in c.detail


def test_the_measured_status_is_recorded(stub):
    stub(_Resp(404))
    assert http_.url_alive("https://x.test/").measured == 404


# ── the ngrok interstitial (a 200 that is NOT the product) ────────────

@pytest.mark.parametrize("body", [
    "<html>You are about to visit a site served by ngrok</html>",
    "<html><a>Visit Site</a></html>",
    "<html>ERR_NGROK_3200</html>",
    "<html>served by abc.ngrok-free.app</html>",
])
def test_the_ngrok_warning_page_is_caught(stub, body):
    # This page is a real 200 with real HTML, so url_alive passes it. This
    # is the only check standing between that and "prototype delivered".
    stub(_Resp(200, text=body))
    c = http_.not_ngrok_interstitial("https://x.ngrok-free.app/acme/")
    assert not c.passed
    assert c.measured


def test_the_real_product_page_is_clean(stub):
    stub(_Resp(200, text="<html><h1>Acme reconciliation</h1></html>"))
    assert http_.not_ngrok_interstitial("https://x.test/acme/").passed


def test_interstitial_check_fails_closed_on_a_network_error(stub):
    stub(httpx.ConnectError("refused"))
    assert not http_.not_ngrok_interstitial("https://x.test/").passed


def test_interstitial_check_rejects_a_file_url(stub):
    stub(_Resp(200))
    assert not http_.not_ngrok_interstitial("file:///tmp/x.html").passed


def test_only_the_head_of_the_body_is_scanned(stub, monkeypatch):
    # The marker scan is bounded to 4000 chars so a huge page cannot make
    # this check slow; a marker past that point is out of scope by design.
    stub(_Resp(200, text=("x" * 5000) + "You are about to visit"))
    assert http_.not_ngrok_interstitial("https://x.test/").passed


# ── content type ──────────────────────────────────────────────────────

def test_content_type_matches_on_prefix(stub):
    stub(_Resp(200, ctype="text/html; charset=utf-8"))
    assert http_.content_type_is("https://x.test/", "text/html").passed


def test_a_wrong_content_type_fails(stub):
    # A prototype URL serving JSON usually means a tunnel/404 page, not a page.
    stub(_Resp(200, ctype="application/json"))
    c = http_.content_type_is("https://x.test/", "text/html")
    assert not c.passed
    assert "application/json" in c.detail


def test_a_missing_content_type_header_fails_closed(stub):
    r = _Resp(200)
    r.headers = {}
    stub(r)
    assert not http_.content_type_is("https://x.test/", "text/html").passed


def test_content_type_fails_closed_on_a_network_error(stub):
    stub(httpx.ReadTimeout("t"))
    assert not http_.content_type_is("https://x.test/", "text/html").passed


def test_content_type_requires_a_live_url(stub):
    stub(_Resp(200))
    assert not http_.content_type_is("", "text/html").passed
    assert not http_.content_type_is("file:///x", "text/html").passed


# ── body size ─────────────────────────────────────────────────────────

def test_a_large_enough_body_passes(stub):
    stub(_Resp(200, content=b"x" * 9000))
    assert http_.body_size_at_least("https://x.test/", 8000).passed


def test_a_too_small_body_fails(stub):
    # A near-empty 200 is the shape of a tunnel error page or a failed build.
    stub(_Resp(200, content=b"x" * 100))
    c = http_.body_size_at_least("https://x.test/", 8000)
    assert not c.passed
    assert c.measured == 100


def test_body_size_is_exact_at_the_boundary(stub):
    stub(_Resp(200, content=b"x" * 8000))
    assert http_.body_size_at_least("https://x.test/", 8000).passed


def test_body_size_fails_closed_on_a_network_error(stub):
    stub(httpx.ConnectError("x"))
    assert not http_.body_size_at_least("https://x.test/", 10).passed


# ── shared behaviour ──────────────────────────────────────────────────

def test_the_ngrok_skip_header_is_sent(monkeypatch):
    # Without it, ngrok serves its interstitial to our own checks and every
    # tunnel-hosted artifact would look broken.
    seen = {}

    def fake_get(url, **kw):
        seen.update(kw.get("headers") or {})
        return _Resp(200)

    monkeypatch.setattr(http_.httpx, "get", fake_get)
    http_.url_alive("https://x.test/")
    assert seen.get("ngrok-skip-browser-warning") == "true"


def test_checks_carry_their_given_name(stub):
    stub(_Resp(200))
    assert http_.url_alive("https://x.test/", name="custom").name == "custom"
