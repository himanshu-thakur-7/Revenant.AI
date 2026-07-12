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

        # 3. Lip-sync avatar via D-ID (optional — degrades to static bubble).
        talking_head: Path | None = None
        avatar_warning: str | None = None
        talking_head_path = state.workspace / "talking-head.mp4"
        if settings.skip_lipsync:
            avatar_warning = "lip-sync skipped (DIRECTOR_SKIP_LIPSYNC=1)"
        else:
            try:
                talking_head = avatar.generate_lipsync_mp4(
                    audio_all, talking_head_path,
                )
            except avatar.DIDError as exc:
                avatar_warning = f"D-ID lip-sync unavailable: {exc}"
            except Exception as exc:  # network hiccup, JSON quirk, etc.
                avatar_warning = f"D-ID unexpected error: {exc}"

        # 4. Record the prototype driving the beats' UI actions, embedding
        #    the talking-head video in the presenter bubble when available.
        try:
            webm = recorder.record_prototype(
                url=prototype_url,
                beats=state.beats,
                beat_durations_s=state.mp3_durations,
                video_dir=state.video_dir,
                presenter_initial=(presenter_name or "R")[:2].upper(),
                presenter_label=(presenter_name or "R")[:16],
                talking_head_path=talking_head,
            )
        except Exception as exc:
            return {"error": f"playwright capture failed: {exc}"}
        state.webm_path = str(webm)

        # 5. Mux narration onto video → MP4.
        mp4_path = state.workspace / "walkthrough.mp4"
        try:
            muxer.mux_to_mp4(Path(webm), audio_all, mp4_path)
        except muxer.MuxError as exc:
            return {"error": f"ffmpeg mux failed: {exc}", "webm_path": str(webm)}
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
