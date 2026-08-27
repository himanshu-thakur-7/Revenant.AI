"""T0 coverage for agents/preferences.py — Phase 3 of the multi-tenant
work. No network: the extractor's LLM call is stubbed so these test the
verification and storage logic, which is where the risk actually is.

The tests that matter most here are the rejection ones. A fabricated
preference has nothing to check it against once stored, and gets injected
into every future prompt for that customer — so "the extractor cannot
record something the founder did not say" is a correctness property, not
a nice-to-have.
"""

from __future__ import annotations

import json

import pytest

import agents.preferences as prefs_mod
import agents.tenancy as tenancy
from agents.preferences import Preference, add, extract, load, render_for_prompt, save


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(tenancy, "REVENANT_HOME", tmp_path)
    monkeypatch.setattr(tenancy, "STARTUPS_DIR", tmp_path / "startups")
    monkeypatch.setattr(tenancy, "LAST_ACTIVE_PATH", tmp_path / "last_active_tenant")
    return tmp_path


def _p(text, quote, kind="feedback"):
    return Preference(text=text, quote=quote, kind=kind, recorded_at="2026-01-01T00:00:00")


def _stub_extract(monkeypatch, payload, *, offline=False):
    """Stub the LLM + offline flag the extractor consults."""
    import ghost.config as config_mod

    class _S:
        pass

    s = _S()
    s.offline = offline
    monkeypatch.setattr(config_mod, "settings", s)

    import ghost.llm as llm_mod
    monkeypatch.setattr(llm_mod, "complete_json", lambda prompt, **kw: payload)


# ── storage round-trip + isolation ────────────────────────────────────

def test_load_missing_is_empty(home):
    assert load("acme") == []


def test_save_then_load_round_trips(home):
    save("acme", [_p("Keep copy short", "keep copy short")])
    got = load("acme")
    assert len(got) == 1
    assert got[0].text == "Keep copy short"


def test_two_tenants_do_not_share_preferences(home):
    save("acme", [_p("Acme prefers terse copy", "terse")])
    save("globex", [_p("Globex prefers formal copy", "formal")])
    assert load("acme")[0].text == "Acme prefers terse copy"
    assert load("globex")[0].text == "Globex prefers formal copy"
    assert len(load("acme")) == 1


def test_corrupt_file_loads_as_empty_not_raise(home):
    tenancy.tenant_home("acme").mkdir(parents=True, exist_ok=True)
    prefs_mod._prefs_path("acme").write_text("{ not json")
    assert load("acme") == []


def test_unknown_kind_is_coerced_not_trusted(home):
    tenancy.tenant_home("acme").mkdir(parents=True, exist_ok=True)
    prefs_mod._prefs_path("acme").write_text(json.dumps({
        "preferences": [{"text": "x", "quote": "x", "kind": "wildly-made-up"}]
    }))
    assert load("acme")[0].kind == "feedback"


# ── dedupe + cap ──────────────────────────────────────────────────────

def test_add_skips_duplicates(home):
    add("acme", [_p("Keep copy short", "short")])
    added = add("acme", [_p("keep   COPY short", "short")])  # same after normalize
    assert added == []
    assert len(load("acme")) == 1


def test_add_returns_only_what_was_actually_new(home):
    add("acme", [_p("A", "a")])
    added = add("acme", [_p("A", "a"), _p("B", "b")])
    assert [p.text for p in added] == ["B"]


def test_cap_drops_oldest(home):
    many = [_p(f"pref {i}", f"q{i}") for i in range(prefs_mod.MAX_PREFERENCES + 10)]
    save("acme", many)
    got = load("acme")
    assert len(got) == prefs_mod.MAX_PREFERENCES
    assert got[-1].text == f"pref {len(many) - 1}"      # newest kept
    assert got[0].text != "pref 0"                       # oldest dropped


# ── THE CORE GUARANTEE: quotes must be real ───────────────────────────

def test_verified_preference_is_kept(home, monkeypatch):
    transcript = "Founder: never use the word revolutionary in our copy."
    _stub_extract(monkeypatch, {"preferences": [
        {"text": "Never use the word revolutionary",
         "quote": "never use the word revolutionary", "kind": "avoid"},
    ]})
    got, rejected = extract(transcript)
    assert len(got) == 1
    assert rejected == []


def test_fabricated_preference_is_discarded(home, monkeypatch):
    """The whole point of the module. The extractor claims a preference
    the founder never expressed; its quote does not appear, so it must be
    dropped entirely rather than stored with lower confidence."""
    transcript = "Founder: never use the word revolutionary in our copy."
    _stub_extract(monkeypatch, {"preferences": [
        {"text": "Prefers a warm, casual, story-driven tone",
         "quote": "we love warm casual storytelling", "kind": "tone"},
    ]})
    got, rejected = extract(transcript)
    assert got == []
    assert len(rejected) == 1
    assert "unverified quote" in rejected[0]


def test_mixed_real_and_fabricated_keeps_only_the_real(home, monkeypatch):
    transcript = "Founder: we are SOC-2 certified. Keep emails under 120 words."
    _stub_extract(monkeypatch, {"preferences": [
        {"text": "SOC-2 certified", "quote": "we are SOC-2 certified", "kind": "brand_fact"},
        {"text": "Wants emoji in every email", "quote": "use lots of emoji", "kind": "tone"},
        {"text": "Emails under 120 words", "quote": "Keep emails under 120 words", "kind": "tone"},
    ]})
    got, rejected = extract(transcript)
    assert sorted(p.text for p in got) == ["Emails under 120 words", "SOC-2 certified"]
    assert len(rejected) == 1


def test_paraphrased_quote_does_not_count_as_verified(home, monkeypatch):
    # Verification must be substring-exact (post-normalization), not fuzzy:
    # a paraphrase is precisely how an invented preference sneaks in.
    transcript = "Founder: please keep every email under 120 words."
    _stub_extract(monkeypatch, {"preferences": [
        {"text": "Short emails", "quote": "keep emails brief", "kind": "tone"},
    ]})
    got, _ = extract(transcript)
    assert got == []


def test_quote_verification_tolerates_whitespace_and_case(home, monkeypatch):
    transcript = "Founder:  NEVER   use   the word revolutionary."
    _stub_extract(monkeypatch, {"preferences": [
        {"text": "Avoid 'revolutionary'", "quote": "never use the word revolutionary",
         "kind": "avoid"},
    ]})
    got, _ = extract(transcript)
    assert len(got) == 1


def test_preference_with_empty_quote_is_rejected(home, monkeypatch):
    _stub_extract(monkeypatch, {"preferences": [
        {"text": "Something confident", "quote": "", "kind": "tone"},
    ]})
    got, rejected = extract("some transcript")
    assert got == []
    assert len(rejected) == 1


def test_empty_extraction_is_a_valid_answer(home, monkeypatch):
    _stub_extract(monkeypatch, {"preferences": []})
    got, rejected = extract("Founder: build for Meesho next.")
    assert got == [] and rejected == []


# ── offline guard ─────────────────────────────────────────────────────

def test_offline_mode_never_extracts(home, monkeypatch):
    """ghost/llm.py returns a canned stub offline; parsing that as a real
    extraction would poison the customer's preferences permanently —
    same class as the offline-stub briefing and offline-judge bugs."""
    _stub_extract(monkeypatch, {"preferences": [
        {"text": "anything", "quote": "anything", "kind": "tone"},
    ]}, offline=True)
    got, rejected = extract("Founder: never say revolutionary")
    assert got == []
    assert rejected and "offline" in rejected[0].lower()


def test_empty_transcript_extracts_nothing(home, monkeypatch):
    _stub_extract(monkeypatch, {"preferences": [{"text": "x", "quote": "x"}]})
    assert extract("   ") == ([], [])


# ── prompt rendering ──────────────────────────────────────────────────

def test_render_empty_is_empty_string(home):
    assert render_for_prompt([]) == ""


def test_render_includes_text_and_labels(home):
    block = render_for_prompt([
        _p("Never say revolutionary", "q", kind="avoid"),
        _p("SOC-2 certified", "q", kind="brand_fact"),
    ])
    assert "Never say revolutionary" in block
    assert "Never do this" in block
    assert "Fact about their product" in block


def test_render_respects_max_chars(home):
    block = render_for_prompt([_p(f"pref {i} " + "x" * 50, "q") for i in range(40)],
                              max_chars=300)
    assert len(block) <= 300


def test_for_startup_resolves_tenant_and_renders(home):
    save("acme", [_p("Never say revolutionary", "q", kind="avoid")])
    assert "Never say revolutionary" in prefs_mod.for_startup("Acme")


def test_for_startup_unknown_startup_is_empty(home):
    assert prefs_mod.for_startup("NoSuchStartupXYZ") == ""
