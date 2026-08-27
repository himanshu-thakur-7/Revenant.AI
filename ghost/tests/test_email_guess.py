"""agents/research/email_guess.py — candidate address derivation.

Worth real coverage because the output is used to email a REAL named
person at a real company. A wrong guess is not a neutral miss: it either
bounces (wasted outreach) or reaches a different actual human at that
company, which is the worse outcome.
"""

from __future__ import annotations

import pytest

from agents.research.email_guess import guess


def emails(*a, **kw):
    return [g["email"] for g in guess(*a, **kw)]


# ── the common path ───────────────────────────────────────────────────

def test_first_last_is_ranked_first():
    # Hunter's public stats put first.last well ahead; the ordering is the
    # whole value of the module, so it is asserted rather than assumed.
    assert emails("Dan", "Zhou", "plaid.com")[0] == "dan.zhou@plaid.com"


def test_returns_several_distinct_candidates():
    got = emails("Dan", "Zhou", "plaid.com")
    assert len(got) == len(set(got)) >= 4


def test_top_caps_the_result_count():
    assert len(emails("Dan", "Zhou", "plaid.com", top=2)) == 2


def test_every_result_names_its_pattern():
    for g in guess("Dan", "Zhou", "plaid.com"):
        assert g["pattern"] and g["email"]


def test_initial_patterns_use_real_initials():
    got = emails("Dan", "Zhou", "plaid.com")
    assert "dzhou@plaid.com" in got


# ── transliteration (a real bug this caught) ──────────────────────────

@pytest.mark.parametrize("first,last,expected", [
    ("José", "Müller", "jose.muller@acme.com"),
    ("Renée", "Zoë", "renee.zoe@acme.com"),
    ("Björn", "Åkesson", "bjorn.akesson@acme.com"),
    ("Łukasz", "Nowak", "ukasz.nowak@acme.com"),   # Ł has no NFKD decomposition
])
def test_accented_names_transliterate_rather_than_lose_letters(first, last, expected):
    # REGRESSION: these characters previously failed the [a-z0-9] filter and
    # were dropped outright — José became "jos", Müller became "mller" — so
    # the top candidate was a confidently wrong address.
    assert emails(first, last, "acme.com")[0] == expected


def test_a_fully_non_latin_name_does_not_produce_a_bare_domain():
    # Nothing survives folding here; the module must return no guess rather
    # than something like "@acme.com" or a domain-only string.
    for e in emails("张", "伟", "acme.com"):
        assert not e.startswith("@")
        assert e.split("@")[0]


# ── name shapes ───────────────────────────────────────────────────────

def test_hyphens_and_apostrophes_are_stripped():
    assert emails("Mary-Jane", "O'Brien", "acme.com")[0] == "maryjane.obrien@acme.com"


def test_a_missing_last_name_skips_patterns_that_need_one():
    got = emails("Dan", "", "plaid.com")
    assert got == ["dan@plaid.com"]


def test_a_missing_first_name_yields_nothing():
    # Every pattern is anchored on the first name; without it there is no
    # honest guess to make.
    assert guess("", "Zhou", "acme.com") == []


def test_whitespace_around_names_is_ignored():
    assert emails("  Dan  ", "  Zhou  ", "acme.com")[0] == "dan.zhou@acme.com"


def test_case_is_normalised():
    assert emails("DAN", "ZHOU", "ACME.COM")[0] == "dan.zhou@acme.com"


def test_middle_names_are_folded_not_split():
    # "Van Der Berg" is one surname token here; folding is the honest
    # behaviour since we cannot know the company's convention.
    assert emails("Jan", "Van Der Berg", "acme.com")[0] == "jan.vanderberg@acme.com"


# ── domain handling ───────────────────────────────────────────────────

@pytest.mark.parametrize("domain", [
    "plaid.com",
    "www.plaid.com",
    "https://plaid.com",
    "https://www.plaid.com",
    "https://www.plaid.com/careers",
    "  PLAID.COM  ",
])
def test_domain_is_normalised_from_any_common_form(domain):
    # A research step may hand over a homepage URL rather than a bare
    # domain; every one of these must yield the same address.
    assert emails("Dan", "Zhou", domain)[0] == "dan.zhou@plaid.com"


def test_a_missing_domain_yields_nothing():
    assert guess("Dan", "Zhou", "") == []
    assert guess("Dan", "Zhou", "   ") == []


def test_subdomains_are_preserved():
    # careers.acme.com is a real mail domain for some companies; we must not
    # "helpfully" strip it down to acme.com.
    assert emails("Dan", "Zhou", "careers.acme.com")[0] == "dan.zhou@careers.acme.com"


# ── output shape invariants ───────────────────────────────────────────

def test_no_result_is_ever_malformed():
    for first, last, dom in [("Dan", "Zhou", "acme.com"), ("Dan", "", "acme.com"),
                             ("José", "Müller", "https://www.acme.com/x")]:
        for e in emails(first, last, dom):
            assert e.count("@") == 1
            assert not e.startswith("@") and not e.endswith("@")
            assert " " not in e
            assert e == e.lower()
