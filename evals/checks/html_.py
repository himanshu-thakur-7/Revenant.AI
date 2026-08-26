"""Structural checks on the prototype's actual fetched HTML.

Every check here re-fetches the URL (or reads the on-disk copy as a
fallback when there's no live URL) rather than trusting anything the
Engineer's own tool call reported.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx

from evals.checks import Check

_HEADERS = {"ngrok-skip-browser-warning": "true"}

_REQUIRED_IDS = ("demo", "demoInput", "demoRun", "demoOutput", "code", "cta")


def _fetch_html(url: str, html_path: str) -> str | None:
    """Prefer the live URL (what a real visitor sees); fall back to the
    on-disk copy only if there's no URL at all, so a check can still run
    against artifacts from before hosting existed. Returns None if
    neither source is available."""
    if url and not url.startswith("file:"):
        try:
            r = httpx.get(url, follow_redirects=True, timeout=15.0, headers=_HEADERS)
            if r.status_code == 200:
                return r.text
        except Exception:  # noqa: BLE001
            pass
    if html_path and Path(html_path).exists():
        return Path(html_path).read_text(encoding="utf-8", errors="replace")
    return None


def element_id_contract(url: str, html_path: str, *, name: str = "element_id_contract") -> Check:
    html = _fetch_html(url, html_path)
    if html is None:
        return Check(name, False, "no HTML available (neither URL nor file resolved)")
    missing = [i for i in _REQUIRED_IDS if f'id="{i}"' not in html and f"id='{i}'" not in html]
    return Check(name, not missing, f"missing: {missing}" if missing else "all present",
                 measured=missing)


def no_external_img(url: str, html_path: str, *, name: str = "no_external_img") -> Check:
    html = _fetch_html(url, html_path)
    if html is None:
        return Check(name, False, "no HTML available")
    hits = re.findall(r'<img[^>]+src=["\']https?://[^"\']+["\']', html, re.I)
    return Check(name, not hits, f"{len(hits)} external <img> tag(s)" if hits else "none found",
                 measured=len(hits))


def demo_input_prefilled(url: str, html_path: str, *, name: str = "demo_input_prefilled",
                          min_chars: int = 40) -> Check:
    html = _fetch_html(url, html_path)
    if html is None:
        return Check(name, False, "no HTML available")
    # crude but effective: grab the element carrying id="demoInput" and
    # measure whichever of value="" or inner text it has.
    m = re.search(r'id=["\']demoInput["\'][^>]*>(.*?)</', html, re.I | re.S)
    inner = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
    m2 = re.search(r'id=["\']demoInput["\'][^>]*value=["\']([^"\']*)["\']', html, re.I)
    value = m2.group(1).strip() if m2 else ""
    content = inner or value
    n = len(content)
    return Check(name, n >= min_chars, f"{n} chars of prefilled content (want >= {min_chars})",
                 measured=n)


def specificity_lint(url: str, html_path: str, *, merchant: str, merchant_domain: str = "",
                     pain: str = "", contact_name: str = "", contact_title: str = "",
                     name: str = "specificity_lint") -> Check:
    """Reuses the Engineer's own linter (agents.engineer.tools) rather
    than re-implementing account-specificity heuristics — one definition
    of 'specific enough', shared by generation-time nudging and eval-time
    scoring."""
    html = _fetch_html(url, html_path)
    if html is None:
        return Check(name, False, "no HTML available")
    from agents.engineer.tools import _specificity_warning

    prospect: dict[str, Any] = {
        "company_name": merchant,
        "company_domain": merchant_domain,
        "fit_rationale": pain,
        "contact": {"name": contact_name, "title": contact_title},
    }
    warning = _specificity_warning(html, prospect)
    return Check(name, warning == "", warning[:300] if warning else "specific enough")


def renders_clean(url: str, *, name: str = "renders_clean", timeout_ms: int = 20_000) -> Check:
    """The one check that proves the interactive demo actually works, not
    just that the HTML contains the right element ids. Loads the page in
    real Chromium, collects console errors, clicks #demoRun, and asserts
    #demoOutput's text actually changed. Requires a live URL (needs a
    real browser context, not the on-disk file, since relative asset
    loads / CSP would behave differently under file://) and the
    playwright browser binary (already installed this session via
    `python -m playwright install chromium`)."""
    if not url or url.startswith("file:"):
        return Check(name, False, "no live URL — needs a real page load, not file://")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return Check(name, False, "playwright not installed (pip install -e '.[media]')")

    from urllib.parse import urlparse
    target_host = urlparse(url).netloc

    def _route(route):
        # set_extra_http_headers() would apply to EVERY request the page
        # makes, including third-party subresources (Google Fonts, etc.) —
        # caught this live: it made Chromium send our ngrok header on the
        # cross-origin font request, which then failed Google's CORS
        # preflight (their Access-Control-Allow-Headers doesn't list an
        # ngrok-specific header) — a false "broken page" caused entirely by
        # the check, not the product. Only inject it for the tunnel's own
        # origin, matching what a real visitor's browser actually sends.
        if urlparse(route.request.url).netloc == target_host:
            route.continue_(headers={**route.request.headers, "ngrok-skip-browser-warning": "true"})
        else:
            route.continue_()

    errors: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route("**/*", _route)
            page.goto(url, timeout=timeout_ms, wait_until="load")

            before = page.locator("#demoOutput").inner_text() if page.locator("#demoOutput").count() else ""
            demo_ran = False
            if page.locator("#demoRun").count():
                try:
                    page.locator("#demoRun").click(timeout=5000)
                    # 800ms was too short — caught live: a real prototype's
                    # #demoOutput genuinely does update on click, but its JS
                    # computes/renders across ~1.5-2s, and 800ms sampled it
                    # mid-transition and called it unchanged. False negative,
                    # not a product bug (verified in a manual replay: same
                    # page, same click, 2000ms wait — output correctly changed).
                    page.wait_for_timeout(2000)
                    after = page.locator("#demoOutput").inner_text() if page.locator("#demoOutput").count() else ""
                    demo_ran = after != before
                except Exception:  # noqa: BLE001
                    demo_ran = False
            browser.close()
    except Exception as exc:  # noqa: BLE001
        return Check(name, False, f"page load failed: {exc!r}")

    ok = not errors and demo_ran
    detail = f"{len(errors)} console error(s); demo output changed on click: {demo_ran}"
    if errors:
        detail += f" — first: {errors[0][:150]}"
    return Check(name, ok, detail, measured={"errors": len(errors), "demo_ran": demo_ran})
