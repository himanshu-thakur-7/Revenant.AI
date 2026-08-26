"""T0 coverage for ghost/llm.py's offline-stub paths — the whole suite runs
with REVENANT_MODE=offline (ghost/tests/conftest.py), so these functions'
`settings.offline` branch is exactly what every test in this repo already
exercises on every LLM-touching call. This file makes that explicit and
guards the specific gap found live this session: complete_json() and
complete_strong_json() returned their offline stub with no trace signal at
all, unlike complete()/complete_strong() which both call
trace.event("offline_stub") — see evals/__init__.py's docstring for the
real bug this caused (a judge call's offline stub was indistinguishable
from a genuine harsh verdict).
"""

from __future__ import annotations

from ghost import llm, trace


def test_complete_offline_returns_stub_and_emits_event(monkeypatch):
    events = []
    monkeypatch.setattr(trace, "event", lambda name, **kw: events.append(name))
    out = llm.complete("prompt", agent="test", offline="stub text")
    assert out == "stub text"
    assert "offline_stub" in events


def test_complete_json_offline_returns_stub_and_emits_event(monkeypatch):
    events = []
    monkeypatch.setattr(trace, "event", lambda name, **kw: events.append(name))
    out = llm.complete_json("prompt", agent="test", offline={"scores": []})
    assert out == {"scores": []}
    assert "offline_stub" in events   # the gap this test guards


def test_complete_strong_offline_returns_stub_and_emits_event(monkeypatch):
    events = []
    monkeypatch.setattr(trace, "event", lambda name, **kw: events.append(name))
    out = llm.complete_strong("prompt", agent="test", offline="stub text")
    assert out == "stub text"
    assert "offline_stub" in events


def test_complete_strong_json_offline_returns_stub_and_emits_event(monkeypatch):
    events = []
    monkeypatch.setattr(trace, "event", lambda name, **kw: events.append(name))
    out = llm.complete_strong_json("prompt", agent="test", offline={"scores": []})
    assert out == {"scores": []}
    assert "offline_stub" in events   # the gap this test guards
