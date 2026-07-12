"""Director — Agent 3. Films a Loom-style walkthrough of the built prototype.

Pipeline:

1. Given the prototype URL + prospect context, the LLM composes a beat script
   (`narration`, `action`, `hold_ms`).
2. Each beat's narration is rendered to MP3 via ElevenLabs (macOS ``say``
   fallback for offline dev).
3. Playwright headless Chromium opens the prototype URL, injects a presenter
   bubble (avatar + audio-reactive pulse), and drives the beats' UI actions.
4. Recording is written to WebM; ffmpeg concatenates the audio tracks and
   muxes them onto the WebM → MP4.
5. The finished MP4 is uploaded to Cloudflare Stream; the iframe URL comes
   back and is returned to the Orchestrator.
"""

from .agent import Director

__all__ = ["Director"]
