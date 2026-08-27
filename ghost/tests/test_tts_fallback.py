"""agents/director/tts.py::narrate — the provider fallback chain.

Worth testing, unlike the individual _*_render functions. Stubbing httpx
to assert that the ElevenLabs payload contains the model id the source
line already says it contains is a change-detector, not a bug-detector,
and the real failure modes there (a changed API, a bad key, exhausted
quota) are exactly what a stub cannot reveal — audio_not_silent covers
that behaviourally against the real artifact instead.

The CHAIN is different. Every branch here has a real consequence: a
walkthrough that ships with no narration, or a film that dies outright.
The provider ORDER is also a deliberate, documented decision (OpenAI
first because ElevenLabs trial keys often 401 on quota_exceeded, burning
seconds on failed retries) — the kind of thing that gets "tidied" by
someone who does not know why it was chosen.

Offline: every renderer and the duration probe are stubbed.
"""

from __future__ import annotations

import platform

import pytest

import agents.director.tts as tts


@pytest.fixture
def chain(tmp_path, monkeypatch):
    """Drive narrate() with fully controllable providers.

    Returns a helper: configure which providers have keys and which
    succeed, then read back the call order.
    """
    calls: list[str] = []

    def _setup(*, eleven_key=True, oai_key=True,
               eleven_ok=True, oai_ok=True, say_ok=True, prefer_eleven=False,
               system="Darwin"):
        class _S:
            elevenlabs_api_key = "k" if eleven_key else None
            elevenlabs_voice_id = "v"
            llm_api_key = "k" if oai_key else None
            openai_api_key = None
        monkeypatch.setattr(tts, "settings", _S())
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        monkeypatch.setenv("REVENANT_PREFER_ELEVENLABS", "1" if prefer_eleven else "0")
        monkeypatch.setattr(platform, "system", lambda: system)
        monkeypatch.setattr(tts, "_measure", lambda p: 12.5)
        monkeypatch.setattr(tts, "_fix_pronunciation", lambda t: t)

        def mk(name, ok):
            def _fn(text, out_path, **kw):
                calls.append(name)
                if not ok:
                    raise RuntimeError(f"{name} exploded")
                out_path.write_bytes(b"audio")
            return _fn

        monkeypatch.setattr(tts, "_elevenlabs_render", mk("elevenlabs", eleven_ok))
        monkeypatch.setattr(tts, "_openai_render", mk("openai", oai_ok))
        monkeypatch.setattr(tts, "_say_render", mk("say", say_ok))
        return calls

    _setup.calls = calls
    return _setup


def run(tmp_path):
    return tts.narrate("hello there", tmp_path / "out.mp3")


# ── ordering ──────────────────────────────────────────────────────────

def test_openai_is_tried_first_by_default(chain, tmp_path):
    calls = chain()
    run(tmp_path)
    assert calls == ["openai"]


def test_the_env_flag_flips_to_elevenlabs_first(chain, tmp_path):
    # A deliberate escape hatch; if the flag silently stopped working the
    # only symptom would be a subtly different voice.
    calls = chain(prefer_eleven=True)
    run(tmp_path)
    assert calls == ["elevenlabs"]


# ── falling through ───────────────────────────────────────────────────

def test_a_failing_first_provider_falls_through(chain, tmp_path):
    # "a dead key never kills the film" — the module's own contract.
    calls = chain(oai_ok=False)
    run(tmp_path)
    assert calls == ["openai", "elevenlabs"]


def test_a_provider_without_a_key_is_skipped_not_attempted(chain, tmp_path):
    # Skipped, not attempted-and-failed: attempting costs a timeout.
    calls = chain(oai_key=False)
    run(tmp_path)
    assert calls == ["elevenlabs"]


def test_both_providers_failing_falls_back_to_macos_say(chain, tmp_path):
    calls = chain(oai_ok=False, eleven_ok=False)
    run(tmp_path)
    assert calls == ["openai", "elevenlabs", "say"]


def test_no_keys_at_all_still_produces_narration_on_macos(chain, tmp_path):
    # The degraded-but-working path a founder hits with an empty .env.
    calls = chain(eleven_key=False, oai_key=False)
    run(tmp_path)
    assert calls == ["say"]


def test_the_first_success_stops_the_chain(chain, tmp_path):
    calls = chain()
    run(tmp_path)
    assert "elevenlabs" not in calls and "say" not in calls


# ── the genuinely fatal case ──────────────────────────────────────────

def test_all_providers_failing_off_macos_raises_actionably(chain, tmp_path):
    # On Linux (the VPS this is heading for) there is no `say`, so this is
    # a real dead end. It must raise a message naming the fix rather than
    # returning a silent zero-length file that later ships as a mute video.
    chain(oai_ok=False, eleven_ok=False, system="Linux")
    with pytest.raises(RuntimeError) as e:
        run(tmp_path)
    msg = str(e.value)
    assert "ELEVENLABS_API_KEY" in msg or "OPENAI_API_KEY" in msg


def test_no_keys_off_macos_raises_rather_than_returning_silence(chain, tmp_path):
    chain(eleven_key=False, oai_key=False, system="Linux")
    with pytest.raises(RuntimeError):
        run(tmp_path)


# ── return contract ───────────────────────────────────────────────────

def test_narrate_returns_the_path_and_a_duration(chain, tmp_path):
    chain()
    path, secs = run(tmp_path)
    assert path == tmp_path / "out.mp3"
    assert secs == 12.5


def test_the_duration_is_measured_after_the_fallback_too(chain, tmp_path):
    # A caller uses this to time the video against the audio; if the
    # fallback path skipped measurement the film would desync.
    chain(oai_ok=False, eleven_ok=False)
    _, secs = run(tmp_path)
    assert secs == 12.5
