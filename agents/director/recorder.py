"""Playwright capture — drives the prototype and injects a presenter bubble.

Not a general-purpose browser automation module; this is the very specific
task of "record a video of the prototype at ``url`` while following a beat
script whose durations were pre-computed from ElevenLabs narrations."

The presenter bubble is a small overlay in the bottom-right of the video —
avatar + audio-reactive pulse ring — injected as a DOM overlay before
recording starts so it's captured for free (no ffmpeg compositing).
"""

from __future__ import annotations

import base64
import shutil
import time
from pathlib import Path
from typing import Any


def _b64_data_uri(path: Path, mime: str) -> str:
    data = path.read_bytes()
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


PRESENTER_CSS = """
#revenant-presenter {
    position: fixed;
    bottom: 26px;
    right: 26px;
    width: 148px;
    height: 148px;
    z-index: 2147483647;
    display: flex;
    align-items: center;
    justify-content: center;
    pointer-events: none;
}
#revenant-presenter .frame {
    width: 128px;
    height: 128px;
    border-radius: 50%;
    overflow: hidden;
    background: linear-gradient(135deg, #52e0c4, #34b8a0);
    box-shadow: 0 12px 46px rgba(82, 224, 196, 0.4),
                0 0 0 3px rgba(255, 255, 255, 0.06);
    position: relative;
    z-index: 2;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #05130f;
    font-family: system-ui, sans-serif;
    font-size: 46px;
    font-weight: 800;
    letter-spacing: -0.02em;
}
#revenant-presenter .frame video {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
}
#revenant-presenter .pulse {
    position: absolute;
    inset: 8px;
    border-radius: 50%;
    border: 2px solid rgba(82, 224, 196, 0.55);
    animation: revenant-pulse 1.9s ease-out infinite;
}
#revenant-presenter .pulse.two { animation-delay: 0.95s; }
@keyframes revenant-pulse {
    0%   { transform: scale(0.85); opacity: 0.85; }
    100% { transform: scale(1.28); opacity: 0; }
}
#revenant-presenter .label {
    position: absolute;
    bottom: -24px;
    left: 50%;
    transform: translateX(-50%);
    font-family: "JetBrains Mono", monospace;
    font-size: 10px;
    letter-spacing: 0.24em;
    color: #7ee0c6;
    text-transform: uppercase;
    white-space: nowrap;
    text-shadow: 0 1px 8px rgba(0, 0, 0, 0.7);
}
"""

# Two bubble variants: a static initial when no talking-head video is
# available, and a <video> bubble when we have a D-ID lip-sync MP4 to play.
STATIC_HTML = """
<div id="revenant-presenter">
    <div class="pulse"></div>
    <div class="pulse two"></div>
    <div class="frame">R</div>
    <div class="label">Revenant</div>
</div>
"""

VIDEO_HTML_TEMPLATE = """
<div id="revenant-presenter">
    <div class="pulse"></div>
    <div class="pulse two"></div>
    <div class="frame">
        <video id="revenant-avatar" src="{video_src}"
               autoplay muted playsinline preload="auto"></video>
    </div>
    <div class="label">Revenant</div>
</div>
"""


def record_prototype(
    url: str,
    beats: list[dict[str, Any]],
    beat_durations_s: list[float],
    video_dir: Path,
    *,
    viewport: tuple[int, int] = (1280, 780),
    presenter_initial: str = "R",
    presenter_label: str = "Revenant",
    talking_head_path: Path | None = None,
) -> Path:
    """Open ``url`` in headless Chromium, execute beats aligned to their
    narration durations, return the path to the recorded WebM file.

    If ``talking_head_path`` is set, a D-ID lip-sync MP4 is embedded in the
    presenter bubble (playing muted, since audio is muxed later). Otherwise
    the bubble shows a static avatar with the letter ``presenter_initial``.
    """
    from playwright.sync_api import sync_playwright  # deferred import

    video_dir.mkdir(parents=True, exist_ok=True)
    session_dir = video_dir / f"session-{int(time.time())}"
    session_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": viewport[0], "height": viewport[1]},
            record_video_dir=str(session_dir),
            record_video_size={"width": viewport[0], "height": viewport[1]},
        )
        page = context.new_page()
        page.goto(url, wait_until="networkidle", timeout=30_000)

        # Inject the presenter bubble first so it's on-screen from beat 1.
        page.add_style_tag(content=PRESENTER_CSS)

        if talking_head_path and talking_head_path.exists():
            # Load the MP4 as a data: URI so Playwright doesn't need to
            # serve it. Small enough (typically < 4 MB for a ~40s clip).
            b64 = _b64_data_uri(talking_head_path, "video/mp4")
            html_literal = VIDEO_HTML_TEMPLATE.format(video_src=b64)
            inject_html = html_literal.replace("`", "\\`")
            page.evaluate(f"""() => {{
                const wrap = document.createElement('div');
                wrap.innerHTML = `{inject_html}`;
                document.body.appendChild(wrap.firstElementChild);
                const label = document.querySelector('#revenant-presenter .label');
                if (label) label.textContent = {presenter_label!r};
                const v = document.querySelector('#revenant-avatar');
                if (v) {{
                    v.muted = true; v.playsInline = true;
                    v.addEventListener('loadedmetadata', () => v.play().catch(()=>{{}}));
                    v.play().catch(()=>{{}});
                }}
            }}""")
        else:
            page.evaluate(f"""() => {{
                const wrap = document.createElement('div');
                wrap.innerHTML = `{STATIC_HTML}`;
                document.body.appendChild(wrap.firstElementChild);
                const frame = document.querySelector('#revenant-presenter .frame');
                const label = document.querySelector('#revenant-presenter .label');
                if (frame) frame.textContent = {presenter_initial!r};
                if (label) label.textContent = {presenter_label!r};
            }}""")

        # A tiny beat 0 so the presenter bubble is on screen before beat 1 fires.
        time.sleep(0.6)

        for beat, dur in zip(beats, beat_durations_s):
            _perform_action(page, beat.get("action") or {"type": "hold"})
            hold_ms = int(beat.get("hold_ms", 500))
            time.sleep(max(dur, 0.2) + hold_ms / 1000.0)

        # A brief tail so the last beat has breathing room.
        time.sleep(0.8)

        # Grab the video path before closing (Playwright rotates it on close)
        video_handle = page.video
        context.close()
        browser.close()

        if video_handle is None:
            raise RuntimeError("playwright reported no recorded video")
        webm_path = Path(video_handle.path())

    # Move the video out of the session dir into `video_dir/walkthrough.webm`
    final_path = video_dir / "walkthrough.webm"
    shutil.move(str(webm_path), str(final_path))
    # Clean up empty session dir
    try:
        session_dir.rmdir()
    except OSError:
        pass
    return final_path


def _perform_action(page, action: dict[str, Any]) -> None:
    kind = (action or {}).get("type", "hold")
    if kind == "hold":
        return
    if kind == "scroll_to":
        sel = action.get("selector", "")
        if sel:
            page.evaluate(
                "sel => { const el = document.querySelector(sel); "
                "if (el) el.scrollIntoView({behavior:'smooth', block:'center'}); }",
                sel,
            )
        return
    if kind == "click":
        sel = action.get("selector", "")
        if sel:
            try:
                page.locator(sel).first.click(timeout=3_000)
            except Exception:
                pass  # non-fatal; the narration still plays
        return
    if kind == "type":
        sel = action.get("selector", "")
        text = action.get("text", "")
        if sel and text:
            try:
                page.locator(sel).first.fill(text, timeout=3_000)
            except Exception:
                pass
        return
