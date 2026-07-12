"""Extract a prospect's brand signals from their live homepage.

The Engineer builds a prototype that should feel like it belongs on the
PROSPECT's site, not a generic template. This module fetches their homepage
(+ its first stylesheets) and distills the visual signals an LLM can actually
use: accent colours, fonts, the wordmark, and hero copy. Best-effort — every
failure degrades to an empty/partial brief, never raises.
"""

from __future__ import annotations

import html as _html
import re
from collections import Counter

import httpx

_UA = "Mozilla/5.0 (compatible; RevenantEngineer/1.0)"
_HEX = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_FONT = re.compile(r"font-family\s*:\s*([^;{}\"']+)", re.I)
_CSSVAR_COLOR = re.compile(r"(--[\w-]*(?:color|bg|brand|accent|primary)[\w-]*)\s*:\s*(#[0-9a-fA-F]{3,6}|rgb[a]?\([^)]+\))", re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_META_DESC = re.compile(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)', re.I)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_H2H3 = re.compile(r"<h[23][^>]*>(.*?)</h[23]>", re.I | re.S)
_ANCHOR = re.compile(r"<a\b[^>]*>(.*?)</a>", re.I | re.S)
_BUTTON = re.compile(r"<button\b[^>]*>(.*?)</button>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_SCRIPT_STYLE = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1>", re.I | re.S)


def _norm_hex(h: str) -> str:
    h = h.lower()
    if len(h) == 4:  # #abc → #aabbcc
        h = "#" + "".join(c * 2 for c in h[1:])
    return h


def _is_interesting(hexv: str) -> bool:
    """Skip near-black, near-white, and pure greys — keep brand accents."""
    try:
        r, g, b = (int(hexv[i:i + 2], 16) for i in (1, 3, 5))
    except ValueError:
        return False
    mx, mn = max(r, g, b), min(r, g, b)
    if mx < 24 or mn > 232:        # near-black / near-white
        return False
    if mx - mn < 16:               # grey (low saturation)
        return False
    return True


def _clean_text(raw: str, *, max_len: int = 140) -> str:
    txt = _SCRIPT_STYLE.sub(" ", raw or "")
    txt = _TAG.sub(" ", txt)
    txt = _html.unescape(txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:max_len].strip()


def _unique_chunks(chunks: list[str], *, limit: int, min_len: int = 3,
                   max_len: int = 90) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in chunks:
        txt = _clean_text(raw, max_len=max_len)
        if len(txt) < min_len:
            continue
        key = re.sub(r"[^a-z0-9]+", "", txt.lower())
        if not key or key in seen:
            continue
        # Skip cookie/legal cruft; keep commercial/product language.
        if any(bad in key for bad in (
            "cookie", "privacy", "terms", "copyright", "login", "signin",
            "subscribe", "javascript", "browser"
        )):
            continue
        seen.add(key)
        out.append(txt)
        if len(out) >= limit:
            break
    return out


def fetch_brand(domain: str, *, timeout: float = 12.0) -> str:
    """Return a compact human-readable brand brief for ``domain``, or ""."""
    domain = (domain or "").strip()
    if not domain:
        return ""
    url = domain if domain.startswith("http") else f"https://{domain}"
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout,
                          headers={"User-Agent": _UA}) as c:
            r = c.get(url)
            if r.status_code >= 400 or "html" not in r.headers.get("content-type", ""):
                return ""
            html = r.text
            # pull in the first couple of same-origin stylesheets for colours
            css = ""
            for m in re.finditer(r'<link[^>]+rel=["\']stylesheet["\'][^>]+href=["\']([^"\']+)', html, re.I):
                href = m.group(1)
                if href.startswith("//"):
                    href = "https:" + href
                elif href.startswith("/"):
                    href = url.rstrip("/") + href
                elif not href.startswith("http"):
                    continue
                try:
                    cr = c.get(href)
                    if cr.status_code < 400:
                        css += "\n" + cr.text[:60_000]
                except httpx.HTTPError:
                    pass
                if len(css) > 120_000:
                    break
    except httpx.HTTPError:
        return ""

    blob = html + css

    # accent colours — frequency-ranked, brand-interesting only
    colors = Counter(_norm_hex(h) for h in _HEX.findall(blob))
    accents = [c for c, _ in colors.most_common(40) if _is_interesting(c)][:6]
    # named brand css vars (highest signal)
    brand_vars = []
    for name, val in _CSSVAR_COLOR.findall(css)[:12]:
        brand_vars.append(f"{name.strip()}={val.strip()}")
    # fonts
    fonts = []
    for f in _FONT.findall(blob):
        fam = f.strip().strip("'\"").split(",")[0].strip().strip("'\"")
        if fam and fam.lower() not in ("inherit", "initial", "unset") and fam not in fonts:
            fonts.append(fam)
        if len(fonts) >= 4:
            break
    # copy
    title = _clean_text((_TITLE.search(html) or [None, ""])[1], max_len=120) if _TITLE.search(html) else ""
    desc = (_META_DESC.search(html).group(1).strip()[:200]) if _META_DESC.search(html) else ""
    h1 = _clean_text((_H1.search(html) or [None, ""])[1], max_len=140) if _H1.search(html) else ""
    headings = _unique_chunks(_H2H3.findall(html), limit=8, min_len=6, max_len=100)
    nav_terms = _unique_chunks(_ANCHOR.findall(html), limit=10, min_len=3, max_len=44)
    ctas = _unique_chunks(_BUTTON.findall(html), limit=5, min_len=3, max_len=44)

    parts = [f"Source: {url}"]
    if title:      parts.append(f"Wordmark/title: {title}")
    if h1:         parts.append(f"Hero headline: {h1}")
    if desc:       parts.append(f"Tagline: {desc}")
    if headings:   parts.append("Visible homepage phrases: " + " · ".join(headings))
    if nav_terms:  parts.append("Nav/category signals: " + " · ".join(nav_terms))
    if ctas:       parts.append("CTA/button language: " + " · ".join(ctas))
    if brand_vars: parts.append("Brand CSS variables: " + " · ".join(brand_vars))
    if accents:    parts.append("Accent colours (hex): " + ", ".join(accents))
    if fonts:      parts.append("Fonts: " + ", ".join(fonts))
    return "\n".join(parts) if len(parts) > 1 else ""
