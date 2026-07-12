"""Vision QA-and-fix pass for a built prototype.

'Multiple agents on the same prototype': the Engineer writes the page, then a
Polisher *looks at the rendered result* (headless screenshot) and fixes the
visual bugs a source-only review can't see — overlapping elements, cramped or
cut-off panels, misalignment, broken spacing, low-contrast text. Runs 1-2
passes until the render is clean. This is what makes a single prototype
foolproof enough to show live.
"""

from __future__ import annotations

import base64
import os
import re
import tempfile
from pathlib import Path

import httpx

from ghost.config import settings

_VISION_MODEL = os.getenv("REVENANT_POLISH_MODEL", "gpt-4o")  # proven vision model


def _openai_key() -> str:
    return (settings.llm_api_key or settings.openai_api_key
            or os.getenv("OPENAI_API_KEY") or "")


def _screenshot(html: str, width: int = 1280, height: int = 900) -> bytes | None:
    """Render the HTML headless and return a full-page PNG (or None on failure)."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "proto.html"
        p.write_text(html, encoding="utf-8")
        try:
            with sync_playwright() as pw:
                b = pw.chromium.launch()
                pg = b.new_page(viewport={"width": width, "height": height})
                pg.goto("file://" + str(p))
                pg.wait_for_timeout(1400)
                png = pg.screenshot(full_page=True)
                b.close()
                return png
        except Exception:
            return None


def _extract_html(text: str) -> str:
    """Pull the HTML doc out of a model reply (fenced block or raw)."""
    m = re.search(r"```(?:html)?\s*(<!doctype.+?|<html.+?)```", text, re.S | re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"(<!doctype html.+</html>)", text, re.S | re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"(<html.+</html>)", text, re.S | re.I)
    if m:
        return m.group(1).strip()
    return ""


_PROMPT = (
    "You are a senior front-end designer doing FINAL QA on a one-page sales "
    "prototype for {merchant} (built by {startup}). The screenshot is the page "
    "rendered at 1280px wide. Find EVERY visual defect and return a corrected, "
    "complete HTML document.\n\n"
    "Look hard for: elements OVERLAPPING each other; panels/cards that are "
    "cramped, clipped, or cut off; text that runs outside its box or is "
    "unreadable (low contrast); broken alignment or uneven spacing; a hero or "
    "section that looks empty or half-built; anything that looks unfinished.\n\n"
    "Rules for the fix:\n"
    "- Prefer normal flow / flexbox / CSS grid over absolute positioning; if two "
    "things overlap, restructure so they don't.\n"
    "- Keep ALL the content, copy, brand colours, the interactive demo, and the "
    "element ids (#demo #demoInput #demoRun #demoOutput #code #cta) intact.\n"
    "- Keep it a single self-contained HTML file (inline CSS/JS, no external "
    "assets except Google Fonts).\n"
    "- Make it look like a polished, funded product — generous spacing, clean "
    "grid, nothing overlapping, everything aligned.\n\n"
    "Return ONLY the full corrected HTML document, nothing else."
)


def polish_html(html: str, *, startup: str, merchant: str, passes: int = 1) -> str:
    """Render → vision-critique → fix, up to ``passes`` times. Returns improved
    HTML (or the original if the pass can't run / fails)."""
    key = _openai_key()
    if not key or not html:
        return html
    base = (settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
    for _ in range(max(1, passes)):
        png = _screenshot(html)
        if not png:
            break
        b64 = base64.b64encode(png).decode()
        try:
            r = httpx.post(
                base + "/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": _VISION_MODEL,
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text":
                            _PROMPT.format(merchant=merchant, startup=startup)
                            + "\n\nCurrent HTML:\n" + html},
                        {"type": "image_url", "image_url":
                            {"url": f"data:image/png;base64,{b64}"}},
                    ]}],
                    "temperature": 0.2,
                    "max_tokens": 16000,
                },
                timeout=180,
            )
            if r.status_code != 200:
                break
            fixed = _extract_html(r.json()["choices"][0]["message"]["content"])
        except Exception:
            break
        # Only accept a fix that's plausibly a full doc (don't shrink to junk).
        if fixed and len(fixed) > max(400, int(len(html) * 0.5)):
            html = fixed
        else:
            break
    return html
