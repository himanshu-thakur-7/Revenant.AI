"""The two pieces that decide whether a walkthrough actually DEMONSTRATES
anything, rather than filming a static page while a voice talks over it.

  director/tools.py::_ensure_actions  -> guarantees a real interaction is
      scripted at all. If it regresses, the video silently becomes a
      narrated screenshot: it plays, it has audio, it passes every T1
      check, and it shows nothing happening.

  director/recorder.py::_perform_action -> executes one scripted action
      against the live page. Its contract is the opposite of strict: a
      bad selector must NEVER kill the recording, because the narration
      is still worth shipping.

Both are testable without a browser (the recorder takes a `page` object,
so a fake one is enough) and both fail silently, which is the criterion.

Deliberately NOT covered: render_walkthrough's TTS/mux/upload chain
around them — that is genuine I/O, and its real failure modes are
already caught behaviourally by the video_ checks against the produced
file.
"""

from __future__ import annotations

import pytest

from agents.director.recorder import _perform_action
from agents.director.tools import _TARGET_ACTIONS, _ensure_actions


def hold(narration="a line"):
    return {"narration": narration, "action": {"type": "hold"}}


def types(action_lists):
    return [(b.get("action") or {}).get("type", "hold") for b in action_lists]


# ── _ensure_actions: something must actually happen ───────────────────

def test_an_all_hold_script_gains_real_actions():
    # The exact case the function exists for: the LLM "frequently emits
    # straight hold beats even when the prompt insists otherwise".
    out = _ensure_actions([hold() for _ in range(5)])
    assert any(t != "hold" for t in types(out))


def test_a_click_is_scripted_so_the_demo_is_exercised():
    # Without a click the recorded demo never runs, and the video shows an
    # input box that is never submitted.
    out = _ensure_actions([hold() for _ in range(6)])
    assert "click" in types(out)


def test_the_opening_hook_beat_is_left_alone():
    # Beat 0 is narration-only by design — the hook. Overwriting it would
    # start the video mid-interaction with no setup.
    out = _ensure_actions([hold("hook"), hold(), hold()])
    assert (out[0].get("action") or {}).get("type", "hold") == "hold"


def test_beats_that_already_act_are_preserved():
    given = [hold("hook"),
             {"narration": "click it", "action": {"type": "click", "selector": "#mine"}}]
    out = _ensure_actions(given)
    kept = [b for b in out if (b.get("action") or {}).get("selector") == "#mine"]
    assert kept, "the LLM's own action was discarded"


def test_the_input_is_not_mutated():
    given = [hold("hook"), hold()]
    before = [dict(b) for b in given]
    _ensure_actions(given)
    assert given == before


def test_upgraded_beats_get_a_dwell_time():
    # An action with no hold_ms flashes past before the viewer sees it.
    out = _ensure_actions([hold("hook"), hold()])
    acted = [b for b in out[1:] if (b.get("action") or {}).get("type") != "hold"]
    assert acted and all(b.get("hold_ms", 0) >= 900 for b in acted)


def test_a_short_script_is_topped_up_with_extra_beats():
    out = _ensure_actions([hold("hook")])
    assert len(out) > 1


def test_topped_up_narration_is_never_duplicated():
    # REGRESSION: reusing one canned line "produced a walkthrough that said
    # the same sentence 3-4 times". Every narration must be distinct.
    out = _ensure_actions([hold("hook")])
    lines = [(b.get("narration") or "").strip().lower() for b in out]
    assert len(lines) == len(set(lines))


def test_a_topped_up_line_never_collides_with_the_llms_own():
    # The function guards against reusing a line the LLM already wrote.
    llm_line = "One click — watch it run live."
    out = _ensure_actions([hold("hook"), {"narration": llm_line,
                                          "action": {"type": "hold"}}])
    lines = [(b.get("narration") or "").strip() for b in out]
    assert lines.count(llm_line) == 1


def test_a_long_script_is_not_padded_further():
    # len(beats) < 8 gates the top-up; a full script must not grow.
    given = [hold(f"line {i}") for i in range(9)]
    assert len(_ensure_actions(given)) == 9


def test_an_empty_script_does_not_crash():
    assert isinstance(_ensure_actions([]), list)


def test_every_scripted_action_is_a_known_shape():
    out = _ensure_actions([hold() for _ in range(6)])
    valid = {t["type"] for t in _TARGET_ACTIONS} | {"hold", "type"}
    for b in out:
        assert (b.get("action") or {}).get("type", "hold") in valid


def test_scripted_actions_carry_a_selector():
    # A selector-less action is a no-op in _perform_action — it would look
    # scripted and do nothing.
    out = _ensure_actions([hold() for _ in range(6)])
    for b in out:
        act = b.get("action") or {}
        if act.get("type", "hold") != "hold":
            assert act.get("selector")


# ── _perform_action: a bad action must never kill the film ────────────

class FakePage:
    """Records what the recorder asked the browser to do."""

    def __init__(self, *, fail=False):
        self.evaluated, self.clicked, self.filled = [], [], []
        self._fail = fail

    def evaluate(self, script, arg=None):
        self.evaluated.append(arg)

    def locator(self, sel):
        page = self

        class _Loc:
            @property
            def first(self):
                return self

            def click(self, **kw):
                if page._fail:
                    raise RuntimeError("element not found")
                page.clicked.append(sel)

            def fill(self, text, **kw):
                if page._fail:
                    raise RuntimeError("element not found")
                page.filled.append((sel, text))

        return _Loc()


def test_a_click_reaches_the_page():
    p = FakePage()
    _perform_action(p, {"type": "click", "selector": "#demoRun"})
    assert p.clicked == ["#demoRun"]


def test_typing_fills_the_target():
    p = FakePage()
    _perform_action(p, {"type": "type", "selector": "#demoInput", "text": "hello"})
    assert p.filled == [("#demoInput", "hello")]


def test_scroll_to_runs_a_scroll():
    p = FakePage()
    _perform_action(p, {"type": "scroll_to", "selector": "#demo"})
    assert p.evaluated == ["#demo"]


def test_hold_does_nothing():
    p = FakePage()
    _perform_action(p, {"type": "hold"})
    assert not (p.clicked or p.filled or p.evaluated)


def test_an_unknown_action_type_is_ignored_not_fatal():
    # A hallucinated action type must not abort a recording that is
    # otherwise fine.
    p = FakePage()
    _perform_action(p, {"type": "teleport", "selector": "#x"})
    assert not (p.clicked or p.filled or p.evaluated)


def test_a_missing_action_is_treated_as_hold():
    p = FakePage()
    _perform_action(p, {})
    _perform_action(p, None)
    assert not (p.clicked or p.filled or p.evaluated)


@pytest.mark.parametrize("action", [
    {"type": "click", "selector": ""},
    {"type": "scroll_to", "selector": ""},
    {"type": "type", "selector": "", "text": "x"},
    {"type": "type", "selector": "#a", "text": ""},
])
def test_incomplete_actions_are_skipped(action):
    p = FakePage()
    _perform_action(p, action)
    assert not (p.clicked or p.filled or p.evaluated)


@pytest.mark.parametrize("action", [
    {"type": "click", "selector": "#gone"},
    {"type": "type", "selector": "#gone", "text": "x"},
])
def test_a_failing_interaction_is_swallowed(action):
    # THE contract. A selector that no longer matches (the prototype
    # changed, the page is slow) must not raise: the narration still
    # plays and the walkthrough still ships. Losing the whole film over
    # one missed click would be far worse than a video where one
    # interaction did not fire.
    _perform_action(FakePage(fail=True), action)   # must not raise
