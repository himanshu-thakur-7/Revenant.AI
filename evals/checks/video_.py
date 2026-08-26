"""Walkthrough-video checks — ffprobe-based, so they catch the exact
failure this session hit once already: a tool reporting a video URL that
turned out to be a dead link (caught by http_.url_alive), and the deeper
version of the same bug class — a URL that resolves but the file behind
it isn't actually a playable video (silent, zero-duration, or truncated).
ffprobe/ffmpeg are already required by agents/director/muxer.py; nothing
new to install.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import httpx

from evals.checks import Check

_HEADERS = {"ngrok-skip-browser-warning": "true"}


def _local_copy(url: str, mp4_path: str) -> Path | None:
    """ffprobe needs a local file. Prefer downloading the live URL (that's
    the artifact a real viewer gets); fall back to the on-disk copy."""
    if url and not url.startswith("file:"):
        try:
            r = httpx.get(url, follow_redirects=True, timeout=30.0, headers=_HEADERS)
            if r.status_code == 200 and r.content:
                tmp = Path(tempfile.mkstemp(suffix=".mp4")[1])
                tmp.write_bytes(r.content)
                return tmp
        except Exception:  # noqa: BLE001
            pass
    if mp4_path and Path(mp4_path).exists():
        return Path(mp4_path)
    return None


def _ffprobe_streams(path: Path) -> list[dict] | None:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return None
        return json.loads(r.stdout).get("streams", [])
    except Exception:  # noqa: BLE001
        return None


def video_url_alive(url: str, *, name: str = "video_url_alive") -> Check:
    from evals.checks.http_ import url_alive
    return url_alive(url, name=name)


def content_type_is_video(url: str, *, name: str = "content_type_video") -> Check:
    from evals.checks.http_ import content_type_is
    return content_type_is(url, "video/mp4", name=name)


def mp4_size_at_least(url: str, mp4_path: str, min_bytes: int = 300_000,
                       *, name: str = "mp4_size") -> Check:
    local = _local_copy(url, mp4_path)
    if local is None:
        return Check(name, False, "no video file available (neither URL nor path resolved)")
    n = local.stat().st_size
    return Check(name, n >= min_bytes, f"{n} bytes (want >= {min_bytes})", measured=n)


def has_video_and_audio_streams(url: str, mp4_path: str,
                                 *, name: str = "has_video_and_audio_streams") -> Check:
    local = _local_copy(url, mp4_path)
    if local is None:
        return Check(name, False, "no video file available")
    streams = _ffprobe_streams(local)
    if streams is None:
        return Check(name, False, "ffprobe failed to read the file (not a valid video)")
    kinds = {s.get("codec_type") for s in streams}
    ok = "video" in kinds and "audio" in kinds
    return Check(name, ok, f"streams found: {sorted(kinds)}", measured=sorted(kinds))


def duration_between(url: str, mp4_path: str, lo_s: float = 20.0, hi_s: float = 200.0,
                     *, name: str = "duration_between") -> Check:
    local = _local_copy(url, mp4_path)
    if local is None:
        return Check(name, False, "no video file available")
    streams = _ffprobe_streams(local)
    if not streams:
        return Check(name, False, "ffprobe failed to read the file")
    durs = [float(s["duration"]) for s in streams if s.get("duration")]
    if not durs:
        return Check(name, False, "no stream reported a duration")
    d = max(durs)
    return Check(name, lo_s <= d <= hi_s, f"{d:.1f}s (want {lo_s}-{hi_s}s)", measured=d)


def audio_not_silent(url: str, mp4_path: str, min_mean_db: float = -60.0,
                     *, name: str = "audio_not_silent") -> Check:
    """Catches the exact failure the ElevenLabs-quota-out / dead-TTS-key
    path would produce: a video with a real audio STREAM (passes
    has_video_and_audio_streams) that is nonetheless silence."""
    local = _local_copy(url, mp4_path)
    if local is None:
        return Check(name, False, "no video file available")
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", str(local), "-af", "volumedetect", "-vn", "-sn", "-dn",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        return Check(name, False, f"ffmpeg failed: {exc!r}")
    m = None
    for line in r.stderr.splitlines():
        if "mean_volume" in line:
            try:
                m = float(line.strip().split(":")[-1].replace("dB", "").strip())
            except ValueError:
                pass
    if m is None:
        return Check(name, False, "could not read mean_volume from ffmpeg output")
    return Check(name, m >= min_mean_db, f"mean_volume={m:.1f}dB (want >= {min_mean_db}dB)",
                 measured=m)
