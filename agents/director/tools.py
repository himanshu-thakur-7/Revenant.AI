"""Tools the Director agent can call.

Two categories:
* **Read tools** — see the prototype URL and prospect context.
* **Do tool** — one big ``render_walkthrough`` that runs the full TTS →
  record → mux → upload chain, plus ``finalize_walkthrough`` to close out.
"""

from __future__ import annotations

from typing import Any

from ghost.config import settings

from ..tools import Tool, tool
from . import avatar, hosting, muxer, recorder, tts
from .walkthrough import WalkthroughState


# ── deterministic action injection ────────────────────────────────
# Nous Hermes-4 often composes beautiful narration but leaves every
# `action` as `{"type": "hold"}`. The Engineer's prototype uses a
# consistent id vocabulary (#inputText, #redactBtn, #outputText, plus
# a #demo section and — when we can add them — #pain, #code, #cta).
# We upgrade the beats *without* touching the narration, so the story
# stays the LLM's but the video actually shows the product in use.

# Ordered target actions the walkthrough must include. If the LLM's
# beats don't already include these, we swap the corresponding beat's
# `hold` action for the target action (leaving the narration intact).
_TARGET_ACTIONS: list[dict[str, Any]] = [
    {"type": "scroll_to", "selector": "#demo, #pain, h2"},
    {"type": "type", "selector": "#inputText",
     "text": ("Patient MRN: 4471029 · Card 4111 1111 1111 1234 · "
              "Total USD $8,420.00 · john.doe@example.com · SSN 123-45-6789")},
    {"type": "click", "selector": "#redactBtn, button:has-text('Redact')"},
    {"type": "scroll_to", "selector": "#outputText, #code, pre code"},
    {"type": "scroll_to", "selector": "#cta, a[href*='pilot'], footer"},
]


def _first_nonhold_index(beats: list[dict], predicate) -> int:
    for i, b in enumerate(beats):
        if predicate(b.get("action") or {}):
            return i
    return -1


def _ensure_actions(beats: list[dict[str, Any]], *,
                    prospect_ctx: dict[str, Any] | None = None,
                    prototype_url: str = "") -> list[dict[str, Any]]:
    """Return a beats list where at least one type / click / scroll action
    fires. Beats that already have real actions are preserved; beats that
    only hold get upgraded to the next unused target action."""
    beats = [dict(b) for b in beats]
    # Which target-action types are already satisfied by the LLM's beats?
    already = {b.get("action", {}).get("type", "hold") for b in beats
               if (b.get("action") or {}).get("type") != "hold"}

    remaining_targets = [t for t in _TARGET_ACTIONS
                         if t["type"] not in already
                         or t["type"] == "scroll_to"]  # need 2 scrolls

    # Fill from beat 2 onward (leave the hook beat #0 alone — it's
    # narration-only). Only overwrite beats whose action is `hold`.
    for beat in beats[1:]:
        if not remaining_targets:
            break
        act = beat.get("action") or {}
        if act.get("type", "hold") == "hold":
            beat["action"] = remaining_targets.pop(0)
            beat["hold_ms"] = max(int(beat.get("hold_ms") or 0), 900)

    # If the LLM emitted too few beats to carry the demo actions, top up —
    # but each new beat gets DISTINCT, action-appropriate narration. NEVER
    # reuse the same line (that produced a walkthrough that said the same
    # sentence 3-4 times). One canned line per action type, used at most once.
    _NARRATION_FOR = {
        "type":      "Here's a real record — exactly the kind of data you handle every day.",
        "click":     "One click, and every identifier is masked in place.",
        "scroll_to": "And here's how it drops into your stack.",
    }
    _SCROLL_LINES = [
        "And here's how it drops into your stack — two lines.",
        "That's the whole pitch. Your data never leaves clean.",
    ]
    scroll_i = 0
    used_lines = {(b.get("narration") or "").strip() for b in beats}
    if remaining_targets and len(beats) < 8:
        for target in remaining_targets:
            ttype = target["type"]
            if ttype == "scroll_to":
                line = _SCROLL_LINES[min(scroll_i, len(_SCROLL_LINES) - 1)]
                scroll_i += 1
            else:
                line = _NARRATION_FOR.get(ttype, "Let me show you the piece that matters.")
            # Guard against colliding with a line the LLM already used.
            if line in used_lines:
                continue
            used_lines.add(line)
            beats.append({"narration": line, "action": target, "hold_ms": 900})
    return beats


def read_tools(prototype_url: str, prospect: dict[str, Any]) -> list[Tool]:

    @tool("Return the prototype URL Engineer just deployed. Call this before "
          "composing the beat script — you need to know what to film.")
    def read_prototype_url() -> str:
        return prototype_url

    @tool("Return the prospect brief (company, contact, pain_evidence, "
          "fit_rationale). Use this to personalise every beat's narration.")
    def read_prospect_context() -> dict[str, Any]:
        return prospect

    return [read_prototype_url, read_prospect_context]


def action_tools(state: WalkthroughState, prototype_url: str) -> list[Tool]:

    @tool(
        "Render the walkthrough end-to-end. Pass `beats` — a list of "
        "{narration, action, hold_ms} dictionaries. `presenter_name` is a "
        "short label shown under the presenter bubble (default 'Revenant'). "
        "Python: TTS per beat (ElevenLabs → macOS `say` fallback) → sends the "
        "concatenated narration to D-ID for a lip-synced talking-head video → "
        "records the prototype in Playwright with the talking head embedded "
        "in the bubble → muxes audio via ffmpeg → publishes the MP4 to a "
        "Cloudflare Pages URL. Returns "
        "{video_url, mp4_path, duration_s, deployer, lipsync}. Call ONCE — "
        "each render burns ElevenLabs + D-ID + Cloudflare credits."
    )
    def render_walkthrough(
        beats: list[dict],
        presenter_name: str = "Revenant",
    ) -> dict[str, Any]:
        if not beats:
            return {"error": "beats list is empty — pass at least one beat"}

        # Guarantee the walkthrough is actionable — the LLM frequently emits
        # straight `hold` beats even when the prompt insists otherwise.
        # We keep the LLM's narration and only *upgrade* the actions.
        beats = _ensure_actions(beats, prototype_url=prototype_url)

        # De-dupe narration: the LLM (and older top-up logic) sometimes repeat
        # the same sentence across beats, which made the AI voice say the same
        # line 3-4 times. Drop any beat whose narration exactly repeats an
        # earlier one (case/space-insensitive); keep its action on the last
        # kept beat so we don't lose a demo interaction.
        _seen: set[str] = set()
        _deduped: list[dict[str, Any]] = []
        for beat in beats:
            key = " ".join((beat.get("narration") or "").lower().split())
            if key and key in _seen:
                # fold this beat's action onto the previous kept beat if that
                # one is just a hold, so the click/type still happens
                if _deduped and (_deduped[-1].get("action") or {}).get("type", "hold") == "hold":
                    _deduped[-1]["action"] = beat.get("action") or {"type": "hold"}
                continue
            if key:
                _seen.add(key)
            _deduped.append(beat)
        beats = _deduped

        # 1. Narrate each beat with ElevenLabs (or macOS `say` fallback).
        state.beats = list(beats)
        state.mp3_paths.clear()
        state.mp3_durations.clear()
        for i, beat in enumerate(beats):
            text = (beat.get("narration") or "").strip()
            if not text:
                return {"error": f"beat {i} has no narration text"}
            mp3 = state.audio_dir / f"beat-{i:02d}.mp3"
            try:
                _, dur = tts.narrate(text, mp3)
            except Exception as exc:
                return {"error": f"narration failed on beat {i}: {exc}"}
            state.mp3_paths.append(str(mp3))
            state.mp3_durations.append(dur)

        # 2. Concatenate the per-beat MP3s into one narration track — needed
        #    both as the D-ID lip-sync input and the final audio track.
        from pathlib import Path
        audio_all = state.audio_dir / "walkthrough.mp3"
        try:
            muxer.concat_audio([Path(p) for p in state.mp3_paths], audio_all)
        except muxer.MuxError as exc:
            return {"error": f"audio concat failed: {exc}"}

        # 3 + 4 run IN PARALLEL — the D-ID lip-sync (~40s) and the Playwright
        #      screen capture (~45s) no longer wait on each other. The head is
        #      composited onto the recording afterward (step 5) instead of
        #      being DOM-injected before recording. Saves ~40s wall-clock.
        import threading
        talking_head: Path | None = None
        avatar_warning: str | None = None
        talking_head_path = state.workspace / "talking-head.mp4"
        _did: dict[str, Any] = {}

        def _run_did() -> None:
            try:
                _did["path"] = avatar.generate_lipsync_mp4(
                    audio_all, talking_head_path)
            except avatar.DIDError as exc:
                _did["warn"] = f"D-ID lip-sync unavailable: {exc}"
            except Exception as exc:  # network hiccup, JSON quirk, etc.
                _did["warn"] = f"D-ID unexpected error: {exc}"

        did_thread: threading.Thread | None = None
        if settings.skip_lipsync:
            avatar_warning = "lip-sync skipped (DIRECTOR_SKIP_LIPSYNC=1)"
        else:
            did_thread = threading.Thread(target=_run_did, daemon=True)
            did_thread.start()

        # Record the prototype with the STATIC bubble as a placeholder. If the
        # D-ID head lands, ffmpeg overlays it exactly on top of that bubble; if
        # D-ID fails, the static bubble stays as the graceful fallback.
        try:
            webm = recorder.record_prototype(
                url=prototype_url,
                beats=state.beats,
                beat_durations_s=state.mp3_durations,
                video_dir=state.video_dir,
                presenter_initial=(presenter_name or "R")[:2].upper(),
                presenter_label=(presenter_name or "R")[:16],
                talking_head_path=None,  # decoupled — composited in step 5
            )
        except Exception as exc:
            return {"error": f"playwright capture failed: {exc}"}
        state.webm_path = str(webm)

        # Join the D-ID worker. Recording already overlapped part of D-ID's
        # queue+render time, but on the trial tier the talk can still be
        # rendering (queue alone is ~3 min). Wait up to the poll budget +
        # headroom so a slow-but-valid talk isn't abandoned AFTER its credit
        # was charged (the exact bug behind §16.1). avatar.POLL_MAX_WAIT is
        # the thread's own ceiling; this join just needs to outlast it.
        if did_thread is not None:
            did_thread.join(timeout=avatar.POLL_MAX_WAIT + 30)
            talking_head = _did.get("path")
            avatar_warning = _did.get("warn")

        # Surface the avatar outcome to the log — without this we were blind to
        # WHY the presenter went missing while D-ID credits were still charged.
        import sys as _sys
        if avatar_warning:
            print(f"[director] ⚠️ presenter missing: {avatar_warning}", file=_sys.stderr, flush=True)
        elif talking_head and talking_head.exists():
            print(f"[director] ✅ talking-head ready: {talking_head} "
                  f"({talking_head.stat().st_size} bytes)", file=_sys.stderr, flush=True)
        else:
            print("[director] ⚠️ presenter missing: D-ID returned no file "
                  "(no warning captured)", file=_sys.stderr, flush=True)

        # 5. Composite the talking head (if any) + mux narration → MP4.
        mp4_path = state.workspace / "walkthrough.mp4"
        try:
            if talking_head and talking_head.exists():
                muxer.composite_and_mux(Path(webm), talking_head, audio_all, mp4_path)
                print(f"[director] ✅ composited head onto walkthrough", file=_sys.stderr, flush=True)
            else:
                muxer.mux_to_mp4(Path(webm), audio_all, mp4_path)
        except muxer.MuxError as exc:
            # composite failed → fall back to a plain mux so we still ship a video
            print(f"[director] ⚠️ COMPOSITE FAILED (head dropped, static bubble "
                  f"shown): {exc}", file=_sys.stderr, flush=True)
            try:
                muxer.mux_to_mp4(Path(webm), audio_all, mp4_path)
            except muxer.MuxError:
                return {"error": f"ffmpeg failed: {exc}", "webm_path": str(webm)}
        state.mp4_path = str(mp4_path)

        # 6. Deploy the workspace to Cloudflare Pages so the MP4 is a URL.
        host = hosting.deploy_walkthrough(mp4_path)
        state.stream_iframe_url = host.get("mp4_url") or None
        state.stream_uid = host.get("base_url") or None

        result: dict[str, Any] = {
            "video_url": host.get("mp4_url", ""),
            "base_url": host.get("base_url", ""),
            "mp4_path": str(mp4_path),
            "duration_s": round(sum(state.mp3_durations), 2),
            "deployer": host.get("deployer", "none"),
            "lipsync": bool(talking_head and talking_head.exists()),
        }
        if avatar_warning:
            result["lipsync_warning"] = avatar_warning
        if host.get("warning"):
            result["host_warning"] = host["warning"]
        return result

    @tool("Finalize the walkthrough. Pass the `video_url` from render_walkthrough "
          "(or empty string if upload was skipped) and a 2-3 sentence summary "
          "of what you filmed. Call LAST.")
    def finalize_walkthrough(video_url: str, summary: str) -> dict[str, Any]:
        state.finalized = True
        state.stream_iframe_url = video_url or state.stream_iframe_url
        return {
            "prospect_slug": state.prospect_slug,
            "workspace": str(state.workspace),
            "video_url": state.stream_iframe_url or "",
            "mp4_path": state.mp4_path or "",
            "duration_s": round(sum(state.mp3_durations), 2),
            "beats": len(state.beats),
            "summary": summary,
        }

    return [render_walkthrough, finalize_walkthrough]
