"""Text-to-speech — ElevenLabs primary, macOS ``say`` fallback for offline dev.

ElevenLabs (live path):
* ``POST /v1/text-to-speech/{voice_id}`` with ``{text, model_id, voice_settings}``.
* Returns MP3 bytes directly.

Fallback (macOS only):
* ``say -o out.aiff <text>`` → ``ffmpeg -i out.aiff out.mp3``.
* Same interface; audio quality is obviously worse but the pipeline runs.

Both paths return the MP3 path and its duration in seconds (measured with
``ffprobe`` so the caller can build a beat-aligned video timeline).
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

import httpx

from ghost.config import settings


_ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech"
# Warm female default — "Bella" from the ElevenLabs voice library. Pairs with
# the Fiona D-ID avatar so the presenter looks + sounds like one person.
# Override with ELEVENLABS_VOICE_ID (settings.elevenlabs_voice_id) — this
# constant is only the last-resort fallback if settings has been cleared.
_DEFAULT_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"


def narrate(text: str, out_path: Path, *, voice_id: str | None = None) -> tuple[Path, float]:
    """Render ``text`` to an MP3 at ``out_path``. Returns (path, duration_seconds)."""
    if settings.elevenlabs_api_key:
        try:
            _elevenlabs_render(text, out_path, voice_id=voice_id or settings.elevenlabs_voice_id
                                or _DEFAULT_VOICE_ID)
            return out_path, _measure(out_path)
        except Exception as exc:  # quota_exceeded (401), network, etc.
            # The docstring promises a `say` fallback — honour it on ANY
            # ElevenLabs failure so a dead/exhausted key never kills the film.
            if platform.system() != "Darwin":
                raise
            print(f"[tts] ElevenLabs failed ({str(exc)[:120]}); falling back to "
                  f"macOS `say`.", file=sys.stderr, flush=True)
            _say_render(text, out_path)
    elif platform.system() == "Darwin":
        _say_render(text, out_path)
    else:
        raise RuntimeError(
            "No ELEVENLABS_API_KEY and macOS `say` fallback isn't available on this "
            "platform. Add ELEVENLABS_API_KEY to .env to run the walkthrough live."
        )
    return out_path, _measure(out_path)


def _elevenlabs_render(text: str, out_path: Path, *, voice_id: str) -> None:
    url = f"{_ELEVEN_URL}/{voice_id}"
    payload = {
        "text": text,
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {
            "stability": 0.55,
            "similarity_boost": 0.75,
            "style": 0.15,
            "use_speaker_boost": True,
        },
    }
    headers = {
        "xi-api-key": settings.elevenlabs_api_key or "",
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    with httpx.stream("POST", url, headers=headers, json=payload, timeout=60) as resp:
        if resp.status_code != 200:
            # Read the body for a clean error before raising.
            body = resp.read()
            raise RuntimeError(f"ElevenLabs {resp.status_code}: {body[:300]!r}")
        with open(out_path, "wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)


def _say_render(text: str, out_path: Path) -> None:
    """macOS-only fallback: `say` → aiff → ffmpeg → mp3.

    Uses a natural female voice (env ``REVENANT_SAY_VOICE``, default Samantha)
    at a slightly relaxed rate; if that voice isn't installed, falls back to the
    system default voice so rendering never fails.
    """
    aiff = out_path.with_suffix(".aiff")
    voice = os.getenv("REVENANT_SAY_VOICE", "Samantha")
    rate = os.getenv("REVENANT_SAY_RATE", "178")
    try:
        subprocess.run(["say", "-v", voice, "-r", rate, "-o", str(aiff), text],
                       check=True, capture_output=True)
    except subprocess.CalledProcessError:
        subprocess.run(["say", "-o", str(aiff), text], check=True, capture_output=True)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(aiff), "-codec:a", "libmp3lame", "-qscale:a", "3",
         str(out_path)],
        check=True, capture_output=True,
    )
    aiff.unlink(missing_ok=True)


def _measure(path: Path) -> float:
    """Duration in seconds via ffprobe. Returns 0.0 if it fails."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        return float(proc.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        return 0.0
