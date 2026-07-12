"""ffmpeg orchestration — concatenate narration MP3s, mux onto WebM → MP4.

Two steps:
1. ``concat_audio`` — build a single audio track from the per-beat MP3s so
   its timeline exactly matches the sequential playback in the video.
2. ``mux_to_mp4`` — mux that audio onto the Playwright WebM, transcoding to
   H.264/AAC (the format Cloudflare Stream ingests without a re-transcode).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class MuxError(RuntimeError):
    pass


def concat_audio(mp3_paths: list[Path], out_path: Path) -> Path:
    """Concatenate MP3 files into one MP3. Idempotent."""
    if not mp3_paths:
        raise MuxError("no MP3s to concatenate")
    if not _ffmpeg():
        raise MuxError("ffmpeg not on PATH")

    if len(mp3_paths) == 1:
        shutil.copy(str(mp3_paths[0]), str(out_path))
        return out_path

    # ffmpeg concat demuxer — write a manifest and let ffmpeg walk it
    manifest = out_path.parent / "audio_concat.txt"
    manifest.write_text("\n".join(f"file '{p.resolve()}'" for p in mp3_paths))
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(manifest),
           "-c", "copy", str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-6:])
        raise MuxError(f"ffmpeg concat exit {proc.returncode}: {tail}")
    manifest.unlink(missing_ok=True)
    return out_path


def mux_to_mp4(webm: Path, audio: Path, out_path: Path) -> Path:
    """Mux ``audio`` onto ``webm`` → MP4 with H.264 + AAC. The audio track
    length is used verbatim; if the video is longer we trim to ``-shortest``.
    """
    if not _ffmpeg():
        raise MuxError("ffmpeg not on PATH")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(webm),
        "-i", str(audio),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-shortest",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-8:])
        raise MuxError(f"ffmpeg mux exit {proc.returncode}: {tail}")
    return out_path


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")
