"""Console behaviour: message rendering, retry detection, cost accounting,
conversation memory, and the chip/state machine.

These are the pieces that decide what a founder actually sees. Several of
them encode bugs that were real — the retry regex missing a phrasing the
gateway genuinely emits, and video URLs being the only linkified content.
"""

from __future__ import annotations

import pytest

# ── walkthrough video rendering (explicitly protected by request) ──────


def test_a_walkthrough_url_renders_a_playable_video(page, js):
    url = "https://example.test/walkthrough-acme/walkthrough.mp4"
    js(
        "const b = addMsg('rev','');"
        f"renderRichMessage(b, 'Director finished:\\n\\n{url}');"
        "return true;"
    )
    assert page.locator(".mediaCard video").count() == 1
    assert page.locator(".mediaCard video").get_attribute("src") == url


def test_the_video_element_is_actually_playable_not_a_link(page, js):
    js(
        "const b = addMsg('rev','');"
        "renderRichMessage(b, 'https://example.test/x/walkthrough.mp4');"
        "return true;"
    )
    assert page.evaluate(
        "() => { const v=document.querySelector('.mediaCard video');"
        "return !!v && v.controls === true; }"
    )


def test_the_same_video_url_twice_renders_one_player(page, js):
    js(
        "const b = addMsg('rev','');"
        "renderRichMessage(b, 'a https://x.test/w.mp4 and again https://x.test/w.mp4');"
        "return true;"
    )
    assert page.locator(".mediaCard video").count() == 1


def test_a_query_string_on_the_video_url_still_renders(page, js):
    js(
        "const b = addMsg('rev','');"
        "renderRichMessage(b, 'https://x.test/w.mp4?token=abc');"
        "return true;"
    )
    assert page.locator(".mediaCard video").count() == 1


def test_a_non_video_url_renders_a_link_not_a_player(page, js):
    js(
        "const b = addMsg('rev','');"
        "renderRichMessage(b, 'prototype at https://x.test/acme/');"
        "return true;"
    )
    assert page.locator(".mediaCard video").count() == 0
    assert page.locator(".msg a").count() >= 1


def test_prototype_and_walkthrough_together_render_both(page, js):
    js(
        "const b = addMsg('rev','');"
        "renderRichMessage(b, 'Prototype: https://x.test/acme/\\n"
        "Walkthrough: https://x.test/w.mp4');"
        "return true;"
    )
    assert page.locator(".mediaCard video").count() == 1
    assert page.locator(".msg a").count() >= 1


# ── retryable-failure detection ───────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "the server is temporarily down",
    "service is temporarily unavailable",
    "server unavailable",
    "gateway unreachable",
    "requested reconnection",
    "connection is lost",
    "connection closed",
    "NetworkError when attempting to fetch",
    "Failed to fetch",
    "Load failed",
    "timeout",
    "502 Bad Gateway",
    "503",
    "504",
    "run failed: 500",
])
def test_transient_failures_are_recognised_as_retryable(js, msg):
    assert js(f"return hasRetryableFailure({msg!r});") is True


@pytest.mark.parametrize("msg", [
    "I couldn't find a prospect matching that brief",
    "No campaign to critique yet",
    "invalid invite code",
    "QA: FAIL — the prototype is too generic",
    "",
])
def test_real_answers_are_not_treated_as_retryable(js, msg):
    # A false positive here silently re-runs a multi-minute, real-money
    # campaign because the model said something with the word "failed".
    assert js(f"return hasRetryableFailure({msg!r});") is False


def test_retry_detection_is_case_insensitive(js):
    assert js("return hasRetryableFailure('GATEWAY UNREACHABLE');") is True


# ── delegate-task preview parsing ─────────────────────────────────────

def test_parse_tasks_splits_a_multi_task_preview(js):
    got = js("return parseTasks('3 tasks: Research A | Research B | Research C');")
    assert got == ["Research A", "Research B", "Research C"]


def test_parse_tasks_handles_a_single_task(js):
    assert js("return parseTasks('1 task: Build the prototype');") == ["Build the prototype"]


def test_parse_tasks_returns_null_for_unrelated_text(js):
    assert js("return parseTasks('just a normal message');") is None
    assert js("return parseTasks('');") is None
    assert js("return parseTasks(null);") is None


def test_parse_tasks_drops_empty_segments(js):
    assert js("return parseTasks('2 tasks: A | | B');") == ["A", "B"]


# ── cost accounting ───────────────────────────────────────────────────

def test_cost_formats_to_three_decimals(js):
    assert js("return fmtCost(0.5);") == "$0.500"
    assert js("return fmtCost(0);") == "$0.000"


def test_cost_handles_junk_without_showing_nan(js):
    # A NaN in the cost readout looks like a broken product.
    for bad in ("null", "undefined", "'abc'", "NaN"):
        assert "NaN" not in js(f"return fmtCost({bad});")


def test_negative_cost_is_clamped_at_zero(js):
    js("setCost(-5); return true;")
    assert js("return document.querySelector('#stCost').textContent;") == "$0.000"


def test_known_tools_carry_a_cost_estimate(js):
    # eventCost(ev, tool) — the tool name is the SECOND argument.
    got = js("return eventCost({}, 'mcp__revenant__build_full_outreach');")
    assert isinstance(got, (int, float)) and got > 0


def test_an_explicit_cost_on_the_event_wins_over_the_estimate(js):
    got = js("return eventCost({cost_usd: 1.23}, 'mcp__revenant__build_prototype');")
    assert got == 1.23


def test_cost_in_cents_is_converted(js):
    assert js("return eventCost({cost_cents: 250}, 'x');") == 2.5


def test_an_unknown_tool_costs_nothing_rather_than_nan(js):
    got = js("return eventCost({}, 'mcp__revenant__brand_new_tool');")
    assert got == 0


# ── conversation memory / console state ───────────────────────────────

def test_remembered_build_appears_in_history_for_the_agent(js):
    # The real shape build_prototype returns, on the tunnel host actually
    # in use — NOT a pages.dev URL. This is the regression that mattered.
    js(
        "rememberBuild('\u2705 Acme: live prototype deployed \u2192 "
        "https://abcd-1-2-3-4.ngrok-free.app/acme/'); return true;"
    )
    hist = js("return recentHistory().map(h=>h.content).join('\\n');")
    assert "ngrok-free.app/acme/" in hist
    assert "CONSOLE STATE" in hist


def test_remembered_film_is_offered_for_the_next_step(js):
    js("rememberBuild('\u2705 Acme: live prototype deployed \u2192 "
       "https://abcd.ngrok-free.app/acme/');"
       "rememberFilm('Director finished Acme\u2019s walkthrough: "
       "https://abcd.ngrok-free.app/walkthrough-acme/walkthrough.mp4');"
       "return true;")
    hist = js("return recentHistory().map(h=>h.content).join('\\n');")
    assert "walkthrough" in hist.lower()
    assert "walkthrough.mp4" in hist


def test_forgetting_a_build_removes_it_from_history(js):
    js("rememberBuild('\u2705 Acme: live prototype deployed \u2192 "
       "https://abcd.ngrok-free.app/acme/'); forgetBuild(); return true;")
    hist = js("return recentHistory().map(h=>h.content).join('\\n');")
    assert "ngrok-free.app/acme/" not in hist


def test_history_is_bounded(js):
    # Unbounded history would grow every turn until requests get slow or
    # get rejected outright.
    js("for(let i=0;i<50;i++){ transcript.push({role:'user',content:'m'+i}); } return true;")
    assert js("return recentHistory().length;") <= 15


def test_long_messages_are_truncated_in_history(js):
    js("transcript.push({role:'user',content:'x'.repeat(20000)}); return true;")
    longest = js("return Math.max(...recentHistory().map(h=>h.content.length));")
    assert longest <= 6000 + 500      # 6000 cap + the CONSOLE STATE lines


# ── chips / suggested next actions ────────────────────────────────────

def test_film_is_offered_after_a_build(page, js):
    js("rememberBuild('\u2705 Acme: live prototype deployed \u2192 "
       "https://abcd.ngrok-free.app/acme/'); updateChips(); return true;")
    chips = page.evaluate("() => [...document.querySelectorAll('.chip')].map(c=>c.textContent)")
    assert any("film" in c.lower() for c in chips)


def test_draft_outreach_is_offered_after_filming(page, js):
    js("rememberBuild('\u2705 Acme: live prototype deployed \u2192 "
       "https://abcd.ngrok-free.app/acme/');"
       "rememberFilm('Director finished Acme\u2019s walkthrough: "
       "https://abcd.ngrok-free.app/walkthrough-acme/walkthrough.mp4');"
       "updateChips(); return true;")
    chips = page.evaluate("() => [...document.querySelectorAll('.chip')].map(c=>c.textContent)")
    assert any("draft" in c.lower() for c in chips)


# ── the invite gate ───────────────────────────────────────────────────

def test_gate_state_survives_a_reload(page, server):
    page.evaluate("() => setGatePassed(true)")
    page.reload(wait_until="domcontentloaded")
    assert page.evaluate("() => gatePassed()") is True


def test_gate_can_be_cleared(page):
    page.evaluate("() => setGatePassed(false)")
    assert page.evaluate("() => gatePassed()") is False


def test_gate_helpers_survive_blocked_storage(page):
    """Private-mode / blocked cookies must not white-screen the console."""
    page.evaluate(
        "() => { Object.defineProperty(window,'localStorage',{"
        "get(){ throw new Error('blocked'); }, configurable:true}); }"
    )
    assert page.evaluate("() => { try { gatePassed(); return true; } catch(e) { return false; } }")
