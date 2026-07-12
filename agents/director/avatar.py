"""D-ID lip-sync avatar client.

Talks endpoint flow (a Talk == "still image + audio → talking-head video"):

    1. ``POST /audios`` with the local MP3 → returns an ``s3://`` URL D-ID's
       own /talks endpoint can consume.
    2. ``POST /talks`` with ``script.audio_url`` + a source image (a stock
       presenter photo). Returns a talk ``id``.
    3. ``GET /talks/{id}`` polled every couple of seconds until
       ``status == "done"``; the response then carries a signed ``result_url``
       — an MP4 of the presenter lip-syncing to the audio.
    4. Download the MP4 to disk so Playwright can embed it via ``<video>``
       in the presenter bubble.

D-ID's free trial gives 12 credits (~12 short talks) — enough for a couple
of end-to-end walkthroughs. On failure we bubble a clean error so the
caller can decide whether to fall back to the static bubble.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

import httpx

from ghost.config import settings


BASE = "https://api.d-id.com"
POLL_SECONDS = 3
POLL_MAX_WAIT = 180

# A stock D-ID clips-presenter portrait — Fiona (business-professional,
# black jacket, classroom backdrop). Old ``DefaultPresenters/*.jpeg`` URLs
# are stale ("Unsupported file url"); the current v2 clips-presenter CDN
# works with the /talks endpoint too. Override with DID_PRESENTER_URL in
# .env to switch presenter without editing code.
DEFAULT_SOURCE_URL = (
    "https://clips-presenters.d-id.com/v2/Fiona_NoHands_BlackJacket_ClassRoom"
    "/1BOeggEufb/dbRUIwY6KY/image.png"
)


class DIDError(RuntimeError):
    pass


def _auth_headers() -> dict[str, str]:
    key = settings.did_api_key or ""
    if not key:
        raise DIDError(
            "DID_API_KEY not set — add it to .env to render the lip-sync avatar."
        )
    b64 = base64.b64encode(key.encode()).decode()
    return {"Authorization": f"Basic {b64}"}


def upload_audio(mp3_path: Path) -> str:
    """Upload an MP3 to D-ID and get back an ``s3://`` URL."""
    with open(mp3_path, "rb") as f:
        files = {"audio": (mp3_path.name, f, "audio/mpeg")}
        resp = httpx.post(f"{BASE}/audios", headers=_auth_headers(),
                          files=files, timeout=60)
    if resp.status_code not in (200, 201):
        raise DIDError(f"audio upload failed {resp.status_code}: {resp.text[:300]}")
    payload = resp.json()
    return payload.get("url", "")


def create_talk(audio_url: str, source_url: str | None = None) -> str:
    """Kick off a talking-head generation. Returns the talk id."""
    body = {
        "source_url": source_url or DEFAULT_SOURCE_URL,
        "script": {
            "type": "audio",
            "audio_url": audio_url,
            "reduce_noise": True,
        },
        "config": {"stitch": True, "result_format": "mp4"},
    }
    resp = httpx.post(f"{BASE}/talks", headers={**_auth_headers(),
                      "Content-Type": "application/json"},
                      json=body, timeout=30)
    if resp.status_code not in (200, 201):
        raise DIDError(f"talk create failed {resp.status_code}: {resp.text[:400]}")
    tid = (resp.json() or {}).get("id", "")
    if not tid:
        raise DIDError(f"talk create returned no id: {resp.text[:200]}")
    return tid


def poll_talk(talk_id: str) -> str:
    """Poll a talk until done. Returns the ``result_url``."""
    deadline = time.monotonic() + POLL_MAX_WAIT
    last_status = "?"
    while time.monotonic() < deadline:
        resp = httpx.get(f"{BASE}/talks/{talk_id}", headers=_auth_headers(),
                         timeout=15)
        if resp.status_code != 200:
            raise DIDError(f"poll failed {resp.status_code}: {resp.text[:200]}")
        payload = resp.json() or {}
        status = payload.get("status", "")
        if status == "done":
            url = payload.get("result_url") or ""
            if not url:
                raise DIDError(f"talk done but no result_url: {payload}")
            return url
        if status == "error" or status == "rejected":
            raise DIDError(f"talk {status}: {payload.get('error', payload)!r}")
        last_status = status
        time.sleep(POLL_SECONDS)
    raise DIDError(f"talk poll timed out after {POLL_MAX_WAIT}s (last status: {last_status})")


def download(url: str, out_path: Path) -> Path:
    """Fetch a signed result URL and write to disk."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, timeout=60, follow_redirects=True) as resp:
        if resp.status_code != 200:
            raise DIDError(f"download failed {resp.status_code}")
        with open(out_path, "wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)
    return out_path


def generate_lipsync_mp4(mp3_path: Path, out_path: Path,
                         *, source_url: str | None = None) -> Path:
    """One-shot: upload audio → create talk → poll → download MP4."""
    audio_url = upload_audio(mp3_path)
    talk_id = create_talk(audio_url, source_url=source_url)
    result_url = poll_talk(talk_id)
    return download(result_url, out_path)
