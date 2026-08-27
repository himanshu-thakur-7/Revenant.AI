"""evals/checks/video_.py — proving a walkthrough video is really a video.

The failure this guards against is subtle: a file that downloads fine,
has the right size, and even has an audio STREAM, but is silent — which
is exactly what a dead TTS key or an exhausted ElevenLabs quota produces.
A founder would send that to a prospect and never know.

Offline: ffprobe/ffmpeg are stubbed, so no binaries and no network.
"""

from __future__ import annotations

import json
import subprocess

import pytest

import evals.checks.video_ as video_


@pytest.fixture
def mp4(tmp_path):
    p = tmp_path / "walkthrough.mp4"
    p.write_bytes(b"\x00" * 400_000)
    return str(p)


@pytest.fixture
def stub_run(monkeypatch):
    """Stub subprocess.run for ffprobe/ffmpeg."""
    def _install(*, stdout="", stderr="", returncode=0, raises=None):
        def fake_run(cmd, **kw):
            if raises:
                raise raises
            return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
        monkeypatch.setattr(video_.subprocess, "run", fake_run)
    return _install


def _streams(*kinds, duration=None):
    out = []
    for k in kinds:
        s = {"codec_type": k}
        if duration is not None:
            s["duration"] = str(duration)
        out.append(s)
    return json.dumps({"streams": out})


# ── stream presence ───────────────────────────────────────────────────

def test_a_real_video_has_both_streams(stub_run, mp4):
    stub_run(stdout=_streams("video", "audio"))
    assert video_.has_video_and_audio_streams("", mp4).passed


def test_a_video_with_no_audio_track_fails(stub_run, mp4):
    # Silent-by-omission: the narration step failed entirely.
    stub_run(stdout=_streams("video"))
    c = video_.has_video_and_audio_streams("", mp4)
    assert not c.passed
    assert "video" in str(c.measured)


def test_an_audio_only_file_fails(stub_run, mp4):
    stub_run(stdout=_streams("audio"))
    assert not video_.has_video_and_audio_streams("", mp4).passed


def test_ffprobe_failing_fails_the_check(stub_run, mp4):
    # returncode != 0 means the file is not a readable video at all.
    stub_run(returncode=1, stderr="moov atom not found")
    c = video_.has_video_and_audio_streams("", mp4)
    assert not c.passed
    assert "not a valid video" in c.detail


def test_unparseable_ffprobe_output_fails_closed(stub_run, mp4):
    stub_run(stdout="this is not json")
    assert not video_.has_video_and_audio_streams("", mp4).passed


def test_a_missing_ffprobe_binary_fails_closed(stub_run, mp4):
    stub_run(raises=FileNotFoundError("ffprobe"))
    assert not video_.has_video_and_audio_streams("", mp4).passed


def test_an_ffprobe_timeout_fails_closed(stub_run, mp4):
    stub_run(raises=subprocess.TimeoutExpired("ffprobe", 30))
    assert not video_.has_video_and_audio_streams("", mp4).passed


def test_no_file_available_fails(stub_run):
    stub_run(stdout=_streams("video", "audio"))
    assert not video_.has_video_and_audio_streams("", "/nonexistent/x.mp4").passed


# ── duration ──────────────────────────────────────────────────────────

def test_a_normal_duration_passes(stub_run, mp4):
    stub_run(stdout=_streams("video", "audio", duration=69.0))
    c = video_.duration_between("", mp4)
    assert c.passed and c.measured == pytest.approx(69.0)


def test_a_too_short_video_fails(stub_run, mp4):
    # A 2-second file usually means recording aborted immediately.
    stub_run(stdout=_streams("video", duration=2.0))
    assert not video_.duration_between("", mp4).passed


def test_a_too_long_video_fails(stub_run, mp4):
    stub_run(stdout=_streams("video", duration=900.0))
    assert not video_.duration_between("", mp4).passed


def test_the_longest_stream_duration_is_used(stub_run, mp4):
    # Audio and video tracks often differ slightly; taking the max avoids
    # judging the file by a truncated track.
    stub_run(stdout=json.dumps({"streams": [
        {"codec_type": "video", "duration": "60.0"},
        {"codec_type": "audio", "duration": "75.0"},
    ]}))
    assert video_.duration_between("", mp4).measured == pytest.approx(75.0)


def test_streams_without_a_duration_field_fail_clearly(stub_run, mp4):
    stub_run(stdout=_streams("video", "audio"))     # no duration key
    c = video_.duration_between("", mp4)
    assert not c.passed
    assert "duration" in c.detail


def test_duration_boundaries_are_inclusive(stub_run, mp4):
    stub_run(stdout=_streams("video", duration=20.0))
    assert video_.duration_between("", mp4, 20.0, 200.0).passed


# ── silence detection (the important one) ─────────────────────────────

def test_normal_narration_passes(stub_run, mp4):
    stub_run(stderr="[Parsed_volumedetect_0 @ 0x1] mean_volume: -29.3 dB")
    c = video_.audio_not_silent("", mp4)
    assert c.passed and c.measured == pytest.approx(-29.3)


def test_a_silent_track_fails(stub_run, mp4):
    # THE case this exists for: a real audio stream carrying no sound,
    # which is what a dead TTS key or an exhausted quota produces. It
    # passes has_video_and_audio_streams and would otherwise ship.
    stub_run(stderr="mean_volume: -91.0 dB")
    c = video_.audio_not_silent("", mp4)
    assert not c.passed
    assert c.measured == pytest.approx(-91.0)


def test_the_silence_threshold_boundary(stub_run, mp4):
    stub_run(stderr="mean_volume: -60.0 dB")
    assert video_.audio_not_silent("", mp4, -60.0).passed


def test_a_positive_mean_volume_parses(stub_run, mp4):
    stub_run(stderr="mean_volume: 0.0 dB")
    assert video_.audio_not_silent("", mp4).passed


def test_the_last_mean_volume_line_wins(stub_run, mp4):
    # ffmpeg can emit more than one; the final summary is the real one.
    stub_run(stderr="mean_volume: -5.0 dB\nmean_volume: -70.0 dB")
    assert not video_.audio_not_silent("", mp4).passed


def test_missing_mean_volume_output_fails_closed(stub_run, mp4):
    # Cannot prove there is sound -> must not pass.
    stub_run(stderr="ffmpeg version 6.0\nno volumedetect output here")
    c = video_.audio_not_silent("", mp4)
    assert not c.passed
    assert "mean_volume" in c.detail


def test_an_unparseable_mean_volume_fails_closed(stub_run, mp4):
    stub_run(stderr="mean_volume: n/a dB")
    assert not video_.audio_not_silent("", mp4).passed


def test_ffmpeg_missing_fails_closed(stub_run, mp4):
    stub_run(raises=FileNotFoundError("ffmpeg"))
    c = video_.audio_not_silent("", mp4)
    assert not c.passed
    assert "ffmpeg failed" in c.detail


def test_silence_check_with_no_file_fails(stub_run):
    stub_run(stderr="mean_volume: -20.0 dB")
    assert not video_.audio_not_silent("", "/nonexistent/x.mp4").passed


# ── size ──────────────────────────────────────────────────────────────

def test_a_normal_sized_mp4_passes(mp4):
    assert video_.mp4_size_at_least("", mp4).passed


def test_a_truncated_mp4_fails(tmp_path):
    # A few KB means the mux or the upload was cut off.
    p = tmp_path / "tiny.mp4"
    p.write_bytes(b"\x00" * 2048)
    c = video_.mp4_size_at_least("", str(p))
    assert not c.passed
    assert c.measured == 2048


def test_a_zero_byte_mp4_fails(tmp_path):
    p = tmp_path / "empty.mp4"
    p.write_bytes(b"")
    assert not video_.mp4_size_at_least("", str(p)).passed


def test_size_check_with_nothing_available_fails():
    assert not video_.mp4_size_at_least("", "/nonexistent/x.mp4").passed
