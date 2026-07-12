"""Fetch a web page → cleaned plain text.

No fancy readability parser — just strip scripts/styles, drop tags, collapse
whitespace. Good enough for the LLM to pull evidence from careers/status/blog
pages. Capped at 20k chars so we don't blow the context window on a huge page.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx


_UA = (
    "Mozilla/5.0 (compatible; RevenantResearch/0.1; +https://revenant.ai/bot)"
)

_SCRIPT_RX = re.compile(r"<(script|style|noscript|svg)\b[^>]*>.*?</\1>", re.I | re.S)
_TAG_RX = re.compile(r"<[^>]+>")
_WS_RX = re.compile(r"[ \t]+")
_NL_RX = re.compile(r"\n{3,}")


def fetch(url: str, *, timeout: float = 15.0, max_chars: int = 20_000) -> dict[str, str]:
    """Return `{url, final_url, title, text, error}`. Never raises."""
    if not _looks_http(url):
        return {"url": url, "error": "not an http(s) URL"}

    try:
        with httpx.Client(follow_redirects=True, timeout=timeout,
                          headers={"User-Agent": _UA, "Accept": "text/html,*/*"}) as client:
            resp = client.get(url)
    except httpx.HTTPError as exc:
        return {"url": url, "error": f"fetch failed: {exc.__class__.__name__}: {exc}"}

    ct = resp.headers.get("content-type", "")
    if resp.status_code >= 400:
        return {"url": url, "final_url": str(resp.url), "error": f"HTTP {resp.status_code}"}
    if "text/html" not in ct and "text/plain" not in ct and "xml" not in ct:
        return {"url": url, "final_url": str(resp.url), "error": f"non-text content-type: {ct}"}

    html = resp.text
    title = _extract_title(html)
    text = _to_text(html)[:max_chars]
    return {
        "url": url,
        "final_url": str(resp.url),
        "title": title,
        "text": text,
    }


def _looks_http(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in {"http", "https"} and bool(p.netloc)
    except ValueError:
        return False


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return (m.group(1).strip() if m else "")[:200]


def _to_text(html: str) -> str:
    s = _SCRIPT_RX.sub(" ", html)
    s = _TAG_RX.sub(" ", s)
    # HTML entity minimal decode
    s = (s.replace("&nbsp;", " ")
           .replace("&amp;", "&")
           .replace("&lt;", "<")
           .replace("&gt;", ">")
           .replace("&#39;", "'")
           .replace("&quot;", '"'))
    s = _WS_RX.sub(" ", s)
    lines = [ln.strip() for ln in s.splitlines()]
    s = "\n".join(ln for ln in lines if ln)
    s = _NL_RX.sub("\n\n", s)
    return s.strip()
