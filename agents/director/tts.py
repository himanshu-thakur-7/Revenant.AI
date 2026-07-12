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


# Pronunciation fixes for TTS — brand names the engines mangle. Applied to the
# spoken narration ONLY; on-screen/UI text is untouched. "boAt" (capital A
# mid-word) is otherwise read as "bo-A-T".
_SPOKEN_FIXES: dict[str, str] = {"boAt": "boat", "BoAt": "boat", "BOAT": "boat"}


def _fix_pronunciation(text: str) -> str:
    for wrong, right in _SPOKEN_FIXES.items():
        text = text.replace(wrong, right)
    return text


def narrate(text: str, out_path: Path, *, voice_id: str | None = None) -> tuple[Path, float]:
    """Render ``text`` to an MP3 at ``out_path``. Returns (path, duration_seconds).

    Voice chain, each falling through on failure so a dead key never kills the
    film: ElevenLabs (if key) → **OpenAI TTS** (natural, uses the LLM key we
    already have) → macOS ``say`` (robotic last resort)."""
    text = _fix_pronunciation(text)
    # 1. ElevenLabs — only if a key is configured.
    if settings.elevenlabs_api_key:
        try:
            _elevenlabs_render(text, out_path, voice_id=voice_id or settings.elevenlabs_voice_id
                                or _DEFAULT_VOICE_ID)
            return out_path, _measure(out_path)
        except Exception as exc:  # quota_exceeded (401), network, etc.
            print(f"[tts] ElevenLabs failed ({str(exc)[:120]}); trying OpenAI TTS.",
                  file=sys.stderr, flush=True)

    # 2. OpenAI TTS — natural voice, reuses the OpenAI key already in .env.
    oai_key = settings.llm_api_key or settings.openai_api_key or os.getenv("OPENAI_API_KEY")
    if oai_key:
        try:
            _openai_render(text, out_path, api_key=oai_key)
            return out_path, _measure(out_path)
        except Exception as exc:
            print(f"[tts] OpenAI TTS failed ({str(exc)[:120]}); falling back to "
                  f"macOS `say`.", file=sys.stderr, flush=True)

    # 3. macOS `say` — last resort.
    if platform.system() == "Darwin":
        _say_render(text, out_path)
    else:
        raise RuntimeError(
            "All TTS providers failed and macOS `say` isn't available on this "
            "platform. Add ELEVENLABS_API_KEY or a working OPENAI_API_KEY to .env."
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


_OPENAI_TTS_URL = "/audio/speech"


def _openai_render(text: str, out_path: Path, *, api_key: str) -> None:
    """OpenAI text-to-speech → MP3. Natural voice (env REVENANT_TTS_VOICE,
    default 'nova'); model REVENANT_TTS_MODEL (default 'tts-1-hd')."""
    base = (settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
    voice = os.getenv("REVENANT_TTS_VOICE", "nova")
    model = os.getenv("REVENANT_TTS_MODEL", "tts-1-hd")
    payload = {"model": model, "voice": voice, "input": text, "response_format": "mp3"}
    with httpx.stream("POST", base + _OPENAI_TTS_URL,
                      headers={"Authorization": f"Bearer {api_key}"},
                      json=payload, timeout=60) as resp:
        if resp.status_code != 200:
            body = resp.read()
            raise RuntimeError(f"OpenAI TTS {resp.status_code}: {body[:200]!r}")
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
