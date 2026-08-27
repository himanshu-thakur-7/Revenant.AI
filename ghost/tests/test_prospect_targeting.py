"""Who gets contacted, and which industry gets hunted.

Two pieces of near-pure logic picked out of otherwise network-bound
modules, on the same criterion: can this fail in a way anyone would
care about?

  agents/research/apollo.py::_title_rank / find_best_contact
      -> decides WHICH HUMAN receives the outreach.
  agents/runner.py::_classify_vertical
      -> decides WHICH INDUSTRY gets prospected at all.

Both fail silently and plausibly. A mis-ranked title emails an intern
instead of the CTO; a mis-classified brief hunts the wrong sector and
every downstream artifact is confidently, uselessly wrong. Nothing
crashes in either case, which is exactly why they are worth pinning.

Deliberately NOT covered here: the request builders around them
(_headers, search_people's payload). Asserting that a payload dict
matches the payload dict is a change-detector -- it goes red on any
edit without saying whether the edit was correct, and it cannot catch
the things that actually break Apollo (a changed API, a dead key, an
exhausted credit balance).

Offline: the HTTP layer is stubbed.
"""

from __future__ import annotations

import pytest

import agents.research.apollo as apollo
from agents.research.apollo import DEFAULT_TITLES, ApolloError, _title_rank


# ── title ranking: who is "best" ──────────────────────────────────────

def test_the_preference_order_is_respected():
    # Lower rank == preferred. A CEO must outrank an engineering manager.
    assert _title_rank("CEO") < _title_rank("Engineering Manager")


def test_ranking_is_case_insensitive():
    assert _title_rank("ceo") == _title_rank("CEO")


def test_a_title_with_surrounding_words_still_matches():
    # Real Apollo titles are messy: "Co-Founder & CEO", "VP Engineering, EMEA".
    assert _title_rank("Co-Founder & CEO") < len(DEFAULT_TITLES)
    assert _title_rank("VP Engineering, EMEA") < len(DEFAULT_TITLES)


def test_an_unlisted_title_sorts_last_rather_than_first():
    # The failure that matters: an unknown title must not win by accident
    # and route the pitch to whoever Apollo happened to return first.
    assert _title_rank("Summer Intern") == len(DEFAULT_TITLES)
    assert _title_rank("Summer Intern") > _title_rank("CTO")


def test_an_empty_title_sorts_last():
    assert _title_rank("") == len(DEFAULT_TITLES)


def test_every_default_title_ranks_ahead_of_an_unknown_one():
    unknown = _title_rank("Office Administrator")
    for t in DEFAULT_TITLES:
        assert _title_rank(t) < unknown


# ── find_best_contact: selection + graceful degradation ───────────────

def _people(*rows):
    return list(rows)


def _person(name="Dan Zhou", title="CTO", pid="a1"):
    return {"name": name, "title": title, "linkedin_url": "https://li/x",
            "apollo_id": pid}


@pytest.fixture
def stub_apollo(monkeypatch):
    def _install(*, people, reveal=None, reveal_raises=None):
        monkeypatch.setattr(apollo, "search_people", lambda *a, **kw: people)

        def _reveal(pid):
            if reveal_raises:
                raise reveal_raises
            return reveal or {}
        monkeypatch.setattr(apollo, "reveal_email", _reveal)
    return _install


def test_the_first_person_is_selected(stub_apollo):
    stub_apollo(people=_people(_person(name="Dan", title="CTO"),
                              _person(name="Sam", title="Intern")),
                reveal={"email": "dan@acme.com", "email_status": "verified"})
    got = apollo.find_best_contact("acme.com")
    assert got["name"] == "Dan"
    assert got["email"] == "dan@acme.com"
    assert got["email_verified"] is True


def test_an_unverified_email_is_flagged_not_silently_trusted(stub_apollo):
    # Sending to an unverified address is a bounce risk the caller must be
    # able to see; it must not be reported as verified.
    stub_apollo(people=_people(_person()),
                reveal={"email": "d@acme.com", "email_status": "guessed"})
    assert apollo.find_best_contact("acme.com")["email_verified"] is False


def test_nobody_found_returns_an_error_shape_not_a_fake_contact(stub_apollo):
    # Returning an empty-but-valid-looking contact would let the pipeline
    # proceed and email nobody.
    stub_apollo(people=[])
    got = apollo.find_best_contact("acme.com")
    assert "error" in got
    assert "name" not in got


def test_a_failed_reveal_degrades_instead_of_raising(stub_apollo):
    # Apollo credit exhausted mid-run. The contact is still useful (name +
    # title) and email_guess can take over -- losing the whole campaign
    # here would be a far worse outcome than an unresolved address.
    stub_apollo(people=_people(_person()),
                reveal_raises=ApolloError("insufficient credits"))
    got = apollo.find_best_contact("acme.com")
    assert got["name"] == "Dan Zhou"
    assert got["email"] == ""
    assert "credits" in got["email_note"]


def test_a_reveal_with_no_email_leaves_the_field_empty(stub_apollo):
    stub_apollo(people=_people(_person()), reveal={})
    got = apollo.find_best_contact("acme.com")
    assert got["email"] == "" and got["email_verified"] is False


def test_the_reveal_backfills_a_missing_name(stub_apollo):
    # Apollo's search endpoint often omits the name; the reveal carries it.
    stub_apollo(people=_people(_person(name="", title="")),
                reveal={"email": "d@acme.com", "name": "Dan", "title": "CTO"})
    got = apollo.find_best_contact("acme.com")
    assert got["name"] == "Dan" and got["title"] == "CTO"


def test_a_present_name_is_not_overwritten_by_the_reveal(stub_apollo):
    stub_apollo(people=_people(_person(name="Dan Zhou")),
                reveal={"email": "d@acme.com", "name": "D. Zhou"})
    assert apollo.find_best_contact("acme.com")["name"] == "Dan Zhou"


def test_runners_up_are_offered_as_alternates(stub_apollo):
    stub_apollo(people=_people(_person(name="A"), _person(name="B"),
                               _person(name="C"), _person(name="D")),
                reveal={"email": "a@acme.com"})
    alts = apollo.find_best_contact("acme.com")["alternates"]
    assert [a["name"] for a in alts] == ["B", "C"]      # bounded to two


def test_no_reveal_is_attempted_without_an_apollo_id(stub_apollo, monkeypatch):
    # "Spends at most one credit" -- an id-less person must not trigger a
    # billable reveal call.
    called = []
    monkeypatch.setattr(apollo, "search_people",
                        lambda *a, **kw: _people(_person(pid="")))
    monkeypatch.setattr(apollo, "reveal_email",
                        lambda pid: called.append(pid) or {})
    apollo.find_best_contact("acme.com")
    assert called == []


# ── vertical classification: which industry gets hunted ───────────────

@pytest.fixture
def classify():
    from agents.runner import _classify_vertical
    return _classify_vertical


@pytest.mark.parametrize("brief,expected", [
    ("find me fintech customers", "fintech"),
    ("find me FinTech customers", "fintech"),
    ("find me fin-tech customers", "fintech"),
    ("find me fin tech customers", "fintech"),
])
def test_spelling_variants_all_reach_the_same_vertical(classify, brief, expected):
    # The normalisation exists because founders type all four. If it
    # regressed, three of them would silently fall through to the LLM
    # fallback -- slower, costlier, and not necessarily the same answer.
    assert classify(brief)[0] == expected


def test_a_tag_synonym_matches_its_vertical(classify):
    # "telehealth" is not the vertical name but is one of its tags.
    assert classify("companies doing telehealth")[0] == "healthtech"


def test_matching_returns_the_tags_for_the_downstream_search(classify):
    name, tags = classify("fintech please")
    assert name == "fintech"
    assert tags and all(isinstance(t, str) for t in tags)


@pytest.mark.parametrize("vertical", [
    "healthtech", "fintech", "insurtech", "legaltech", "edtech",
    "cybersecurity", "saas",
])
def test_every_known_vertical_is_reachable_by_its_own_name(classify, vertical):
    assert classify(f"sell into {vertical}")[0] == vertical


def test_an_unknown_vertical_falls_back_without_crashing(classify):
    # Offline the LLM returns its stub; the contract is that a caller
    # always gets a usable (name, tags) pair rather than an exception.
    name, tags = classify("companies that make artisanal cheese")
    assert isinstance(name, str) and name
    assert isinstance(tags, list) and tags


def test_an_empty_brief_still_returns_a_usable_pair(classify):
    name, tags = classify("")
    assert isinstance(name, str) and isinstance(tags, list) and tags
