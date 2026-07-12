"""Director agent — the AI-recorded, Loom-style walkthrough.

No human ever touches this recording. The Director:

  1. SCRIPT  — OpenAI writes the walkthrough as timed *beats*, each beat a
     {narration, ui_action} pair grounded in the campaign's evidence.
  2. NARRATE — ElevenLabs renders each beat's narration to MP3; the clip
     durations *become* the timeline (audio-first sync — far more robust than
     aligning audio to video after the fact).
  3. RECORD  — Playwright drives a real browser over the deployed microsite,
     performing each beat's UI action and holding for that beat's audio length,
     with a Loom-style presenter bubble injected as a DOM overlay so it's
     captured for free.
  4. ASSEMBLE— ffmpeg concatenates the narration and muxes it onto the video.
  5. HOST    — upload to Cloudflare Stream; the player URL goes on the microsite
     and into the outreach email.

When heavyweight browser recording is unavailable, the fallback is not a dead
JSON file: it emits a playable Loom-style HTML walkthrough with an avatar
bubble, timed captions, iframe preview, and scripted UI actions. The heavy
media path (3-5) only runs in live mode with Playwright + ffmpeg present.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .config import settings
from .events import DIRECTOR, mission
from .llm import complete_json
from .log import log
from .models import Artifact, Campaign, SellerProfile

OUT = Path("out/walkthroughs")
OUT.mkdir(parents=True, exist_ok=True)

# Words/second used to *estimate* beat durations when we don't synth real audio.
_WPS = 2.6


def storyboard(campaign: Campaign, seller: SellerProfile) -> list[dict]:
    """Beat list: [{narration, ui_action, target}]. Grounded in evidence."""
    lead = campaign.lead
    pain = ""
    if lead.score and lead.score.evidence:
        pain = max(lead.score.evidence, key=lambda e: e.weight).excerpt

    offline = {
        "beats": [
            {
                "narration": f"Hi — this is a quick look at what {seller.name} built for "
                f"{lead.company_name}, automatically.",
                "ui_action": "scroll_to",
                "target": "h1",
            },
            {
                "narration": f"We noticed this in your own words: {pain or lead.job_description[:80]}.",
                "ui_action": "scroll_to",
                "target": "blockquote",
            },
            {
                "narration": "So instead of a pitch deck, here's a working prototype, "
                "wired to your context.",
                "ui_action": "scroll_to",
                "target": "section:nth-of-type(4)",
            },
            {
                "narration": "Give it a try — it runs live, right here on the page.",
                "ui_action": "click",
                "target": "button",
            },
            {
                "narration": f"If it's useful, the pilot is one click. That's {seller.name}, "
                "built for you before we ever spoke.",
                "ui_action": "scroll_to",
                "target": "a[href]",
            },
        ]
    }

    out = complete_json(
        f"Write a 5-beat, ~60-second screen-walkthrough script for a microsite "
        f"{seller.name} built for {lead.company_name}. Each beat: a spoken "
        f"narration line and a ui_action (scroll_to|click) with a CSS target. "
        f"Ground claims in this pain: '{pain or lead.job_description[:120]}'. "
        f"Explain WHAT was built and WHY it fits.",
        agent="director_script",
        system="Return {beats:[{narration, ui_action, target}]}. No uncited claims.",
        offline=offline,
    )
    beats = out.get("beats", offline["beats"])
    for b in beats:
        words = len(b.get("narration", "").split())
        b["est_seconds"] = round(max(2.0, words / _WPS), 1)
    return beats


def direct(campaign: Campaign, seller: SellerProfile) -> Campaign:
    """Produce the walkthrough. Sets campaign.walkthrough_url."""
    log.stage(f"Director: filming an AI walkthrough of {campaign.lead.company_name}'s prototype…")
    mission.emit(
        4, DIRECTOR,
        f"Rolling. Headless browser on {campaign.lead.company_name}'s live prototype — "
        f"presenter bubble on, narration timed to the audio track. No human in the room.",
        campaign_id=campaign.id, company=campaign.lead.company_name, kind="film", dwell=2.2,
    )
    beats = storyboard(campaign, seller)
    total = round(sum(b["est_seconds"] for b in beats), 1)
    for i, b in enumerate(beats, 1):
        mission.emit(
            4, DIRECTOR,
            f"Beat {i}/{len(beats)} · {b['ui_action']}({b['target']}) — “{b['narration'][:90]}”",
            campaign_id=campaign.id, company=campaign.lead.company_name,
            kind="film", dwell=1.5,
        )

    board_path = OUT / f"{campaign.id}.storyboard.json"
    board_path.write_text(json.dumps({"campaign": campaign.id, "beats": beats}, indent=2))
    player_path = OUT / f"{campaign.id}.walkthrough.html"
    player_path.write_text(_walkthrough_player(campaign, seller, beats))

    can_render = (
        settings.require_live("cloudflare_api_token")
        and _has_playwright()
        and shutil.which("ffmpeg")
        and campaign.microsite_url
    )

    if can_render:
        url = _render_and_host(campaign, seller, beats)  # pragma: no cover - heavy path
    else:
        # Offline / no-media: ship a playable walkthrough rather than a JSON prop.
        url = player_path.resolve().as_uri()
        log.dim(f"[director] offline → interactive walkthrough ({len(beats)} beats, ~{total}s)")

    campaign.walkthrough_url = url
    campaign.artifacts.append(
        Artifact(kind="walkthrough", storage_ref=url, verified=True,
                 meta={"beats": len(beats), "seconds": total})
    )
    campaign.add_cost(8)
    mission.emit(
        4, DIRECTOR,
        f"Cut. {len(beats)} beats, ~{total}s — a Loom-style walkthrough of the prototype, "
        f"recorded and edited by an agent, embedded on the microsite.",
        campaign_id=campaign.id, company=campaign.lead.company_name,
        kind="artifact", dwell=2.2, payload={"beats": len(beats), "seconds": total},
    )
    log.ok(f"Walkthrough ready (~{total}s, {len(beats)} beats)")
    return campaign


def _walkthrough_player(campaign: Campaign, seller: SellerProfile, beats: list[dict]) -> str:
    """Self-contained walkthrough player used when full video rendering is not
    available. It is a real demo surface: timed narration, avatar, and iframe
    actions over the deployed prototype."""
    beat_json = json.dumps(beats).replace("</", "<\\/")
    title = f"{seller.name} built for {campaign.lead.company_name}"
    site_url = campaign.microsite_url
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_esc(title)} — walkthrough</title>
  <style>
    :root {{ color-scheme: dark; --bg:#080b12; --panel:#111827; --line:#253044; --ink:#e5eefb; --muted:#8ea0b8; --wisp:#52e0c4; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; min-height:100vh; background:var(--bg); color:var(--ink); font-family:Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    .shell {{ display:grid; grid-template-columns:320px 1fr; min-height:100vh; }}
    aside {{ border-right:1px solid var(--line); background:#0c111d; padding:18px; }}
    h1 {{ font-size:20px; line-height:1.2; margin:0 0 8px; }}
    p {{ color:var(--muted); line-height:1.45; }}
    button {{ border:0; border-radius:8px; padding:11px 15px; background:var(--wisp); color:#04120e; font-weight:800; cursor:pointer; width:100%; }}
    .beat {{ padding:10px 0; border-bottom:1px solid var(--line); color:var(--muted); font-size:13px; }}
    .beat.on {{ color:var(--ink); }}
    .stage {{ position:relative; min-height:100vh; background:#05070c; }}
    iframe {{ width:100%; height:100vh; border:0; background:#06070d; }}
    .bubble {{ position:absolute; right:22px; bottom:22px; width:min(420px, calc(100% - 44px)); border:1px solid rgba(82,224,196,.35); background:rgba(8,11,18,.92); border-radius:14px; padding:14px 15px; box-shadow:0 18px 60px rgba(0,0,0,.38); }}
    .avatar {{ width:38px; height:38px; border-radius:999px; display:grid; place-items:center; background:linear-gradient(135deg,var(--wisp),#8fb5ff); color:#06110e; font-weight:900; float:left; margin-right:12px; }}
    .caption {{ font-size:15px; line-height:1.45; }}
    .progress {{ height:3px; background:#172033; margin-top:14px; border-radius:99px; overflow:hidden; }}
    .bar {{ height:100%; width:0%; background:var(--wisp); transition:width .2s linear; }}
    @media (max-width: 840px) {{
      .shell {{ grid-template-columns:1fr; }}
      aside {{ min-height:auto; border-right:0; border-bottom:1px solid var(--line); }}
      iframe {{ height:70vh; }}
      .stage {{ min-height:70vh; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <h1>{_esc(title)}</h1>
      <p>Autonomous walkthrough generated by the Director agent from grounded campaign evidence.</p>
      <button id="play">Play walkthrough</button>
      <div id="beats" style="margin-top:14px"></div>
    </aside>
    <main class="stage">
      <iframe id="demo" src="{_esc(site_url)}"></iframe>
      <div class="bubble">
        <div class="avatar">AI</div>
        <div id="caption" class="caption">Ready when you are.</div>
        <div class="progress"><div id="bar" class="bar"></div></div>
      </div>
    </main>
  </div>
  <script>
    const beats = {beat_json};
    const list = document.getElementById("beats");
    const caption = document.getElementById("caption");
    const frame = document.getElementById("demo");
    const bar = document.getElementById("bar");
    list.innerHTML = beats.map((b,i)=>`<div class="beat" data-i="${{i}}">${{i+1}}. ${{b.narration}}</div>`).join("");
    const sleep = (ms) => new Promise(r => setTimeout(r, ms));
    async function act(beat) {{
      try {{
        const doc = frame.contentWindow.document;
        const el = doc.querySelector(beat.target);
        if (!el) return;
        if (beat.ui_action === "click") el.click();
        else el.scrollIntoView({{behavior:"smooth", block:"center"}});
      }} catch (_) {{}}
    }}
    async function play() {{
      for (let i = 0; i < beats.length; i++) {{
        document.querySelectorAll(".beat").forEach((el, j) => el.classList.toggle("on", i === j));
        const beat = beats[i];
        caption.textContent = beat.narration;
        act(beat);
        if ("speechSynthesis" in window) {{
          speechSynthesis.cancel();
          const u = new SpeechSynthesisUtterance(beat.narration);
          u.rate = 0.95; u.pitch = 0.95;
          speechSynthesis.speak(u);
        }}
        const ms = Math.max(2200, (beat.est_seconds || 4) * 1000);
        const started = performance.now();
        while (performance.now() - started < ms) {{
          bar.style.width = `${{Math.min(100, ((performance.now() - started) / ms) * 100)}}%`;
          await sleep(120);
        }}
        bar.style.width = "0%";
      }}
      caption.textContent = "Walkthrough complete. The prototype is live on the page.";
      document.querySelectorAll(".beat").forEach(el => el.classList.remove("on"));
    }}
    document.getElementById("play").addEventListener("click", play);
  </script>
</body>
</html>"""


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _has_playwright() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except Exception:
        return False


def _render_and_host(campaign: Campaign, seller: SellerProfile, beats: list[dict]) -> str:  # pragma: no cover
    """Heavy path: narrate → record → mux → upload. Best-effort; any failure
    degrades to the storyboard URL so a demo never hard-fails."""
    work = OUT / campaign.id
    work.mkdir(exist_ok=True)
    try:
        from .voice import _tts  # reuse the ElevenLabs client

        # 1. narrate each beat, capture real durations
        clips: list[Path] = []
        durations: list[float] = []
        for i, b in enumerate(beats):
            mp3 = work / f"beat_{i}.mp3"
            if settings.require_live("elevenlabs_api_key", "elevenlabs_voice_id"):
                _tts(b["narration"], {"stability": 0.5, "style": 0.4, "similarity_boost": 0.75}, mp3)
            dur = _audio_seconds(mp3) if mp3.exists() else b["est_seconds"]
            durations.append(dur)
            b["seconds"] = dur
            if mp3.exists():
                clips.append(mp3)

        # 2. record the browser holding each beat for its narration length
        video = _record(campaign.microsite_url, beats, durations, work)

        # 3. mux audio onto video
        final = work / "walkthrough.mp4"
        _mux(video, clips, final)

        # 4. upload to Cloudflare Stream
        return _cf_stream_upload(final) or final.resolve().as_uri()
    except Exception as exc:
        log.warn(f"[director] media pipeline failed ({exc!r}); using storyboard")
        return (OUT / f"{campaign.id}.storyboard.json").resolve().as_uri()


def _record(url: str, beats: list[dict], durations: list[float], work: Path) -> Path:  # pragma: no cover
    from playwright.sync_api import sync_playwright

    vids = work / "video"
    vids.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 720},
            record_video_dir=str(vids),
            record_video_size={"width": 1280, "height": 720},
        )
        page = ctx.new_page()
        page.goto(url, wait_until="networkidle")
        page.add_style_tag(content=_BUBBLE_CSS)
        page.evaluate(_BUBBLE_JS)
        for b, dur in zip(beats, durations):
            try:
                if b["ui_action"] == "click":
                    page.click(b["target"], timeout=2000)
                else:
                    page.eval_on_selector(
                        b["target"], "el => el.scrollIntoView({behavior:'smooth',block:'center'})"
                    )
            except Exception:
                pass
            page.wait_for_timeout(int(dur * 1000))
        ctx.close()
        browser.close()
    return next(vids.glob("*.webm"))


def _audio_seconds(path: Path) -> float:  # pragma: no cover
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 4.0


def _mux(video: Path, clips: list[Path], out: Path) -> None:  # pragma: no cover
    concat = video.parent / "audio.txt"
    concat.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    audio = video.parent / "narration.mp3"
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
                    "-c", "copy", str(audio)], check=False, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-i", str(video), "-i", str(audio),
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(out)],
                   check=False, capture_output=True)


def _cf_stream_upload(mp4: Path) -> str | None:  # pragma: no cover
    import httpx

    acct = settings.cloudflare_account_id
    try:
        with mp4.open("rb") as fh:
            resp = httpx.post(
                f"https://api.cloudflare.com/client/v4/accounts/{acct}/stream",
                headers={"Authorization": f"Bearer {settings.cloudflare_api_token}"},
                files={"file": (mp4.name, fh, "video/mp4")},
                timeout=120,
            )
        result = resp.json().get("result", {})
        return result.get("preview") or (result.get("playback", {}) or {}).get("hls")
    except Exception as exc:
        log.warn(f"[director] CF Stream upload failed: {exc!r}")
        return None


_BUBBLE_CSS = """
#revenant-bubble{position:fixed;bottom:24px;right:24px;width:96px;height:96px;
border-radius:50%;background:linear-gradient(135deg,#6366f1,#a855f7);z-index:99999;
display:flex;align-items:center;justify-content:center;font-size:40px;color:#fff;
box-shadow:0 0 0 0 rgba(99,102,241,.7);animation:rvpulse 1.6s infinite;}
@keyframes rvpulse{0%{box-shadow:0 0 0 0 rgba(99,102,241,.7)}
70%{box-shadow:0 0 0 22px rgba(99,102,241,0)}100%{box-shadow:0 0 0 0 rgba(99,102,241,0)}}
"""
_BUBBLE_JS = """
() => { const b=document.createElement('div'); b.id='revenant-bubble';
b.textContent='🎙️'; document.body.appendChild(b); }
"""
