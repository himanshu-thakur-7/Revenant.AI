"""agents/orchestrator/tools.py — prospect parsing and the run memo.

Two things worth locking down here, both about money and both driven by
LLM-supplied input:

1. _parse_prospect takes a `prospect_json` argument straight from the
   model. It must return an ERROR STRING the model can read and correct,
   never raise — a raise ends the turn, a string gets a retry.

2. _RUN_MEMO exists because the Engineer once double-built a prototype
   in a single chain (see the comment in the module). A duplicate build
   is ~90s and real API spend, so the memo is a cost control, not an
   optimisation.

Offline: pure functions, no LLM, no network.
"""

from __future__ import annotations

import pytest

import agents.orchestrator.tools as otools
from agents.orchestrator.tools import _memo_key, _parse_prospect


@pytest.fixture(autouse=True)
def clean_memo():
    otools._RUN_MEMO.clear()
    yield
    otools._RUN_MEMO.clear()


# ── _parse_prospect ───────────────────────────────────────────────────

def test_a_dict_passes_through():
    d = {"company_name": "Acme"}
    assert _parse_prospect(d) is d


def test_a_json_string_is_parsed():
    got = _parse_prospect('{"company_name": "Acme"}')
    assert got["company_name"] == "Acme"


def test_surrounding_whitespace_is_tolerated():
    assert _parse_prospect('  {"company_name": "Acme"}  ')["company_name"] == "Acme"


def test_double_encoded_json_is_unwrapped():
    # The Nous quirk again: an object wrapped in another JSON string.
    got = _parse_prospect('"{\\"company_name\\": \\"Acme\\"}"')
    assert isinstance(got, dict)
    assert got["company_name"] == "Acme"


def test_invalid_json_returns_a_readable_error_not_a_raise():
    # A raise here ends the agent's turn; a string is something the model
    # can read and correct on its next step.
    got = _parse_prospect("{not json")
    assert isinstance(got, str)
    assert "not valid JSON" in got


def test_the_error_names_the_position_so_the_model_can_fix_it():
    got = _parse_prospect('{"a": }')
    assert "pos" in got


def test_a_json_array_is_rejected_with_its_type():
    got = _parse_prospect('["Acme"]')
    assert isinstance(got, str)
    assert "list" in got


def test_a_bare_scalar_is_rejected():
    assert isinstance(_parse_prospect("42"), str)
    assert isinstance(_parse_prospect('"just a string"'), str)


def test_a_non_string_non_dict_argument_is_rejected_by_type():
    got = _parse_prospect(12345)
    assert isinstance(got, str)
    assert "int" in got


def test_none_is_rejected_cleanly():
    assert isinstance(_parse_prospect(None), str)


def test_an_empty_string_is_rejected_cleanly():
    assert isinstance(_parse_prospect(""), str)


def test_a_nested_prospect_object_survives_intact():
    got = _parse_prospect(
        '{"company_name": "Acme", "contact": {"name": "Dan", "title": "Head"}}')
    assert got["contact"]["title"] == "Head"


# ── the run memo (a cost control) ─────────────────────────────────────

def test_the_key_is_tool_plus_company():
    assert _memo_key("engineer", {"company_name": "Acme"}) == ("engineer", "acme")


def test_company_matching_ignores_case_and_padding():
    # The model rarely re-emits a company name byte-identically; without
    # folding, the memo would miss and the expensive stage would re-run.
    a = _memo_key("engineer", {"company_name": "Acme"})
    b = _memo_key("engineer", {"company_name": "  ACME  "})
    assert a == b


def test_different_tools_do_not_share_a_memo_entry():
    # Filming after building must not be served the build's cached result.
    assert _memo_key("engineer", {"company_name": "Acme"}) != \
           _memo_key("director", {"company_name": "Acme"})


def test_different_companies_do_not_collide():
    assert _memo_key("engineer", {"company_name": "Acme"}) != \
           _memo_key("engineer", {"company_name": "Globex"})


def test_a_missing_company_name_still_produces_a_usable_key():
    assert _memo_key("engineer", {}) == ("engineer", "")


def test_a_null_company_name_does_not_raise():
    assert _memo_key("engineer", {"company_name": None}) == ("engineer", "")


def test_the_memo_survives_a_repeat_lookup():
    key = _memo_key("engineer", {"company_name": "Acme"})
    otools._RUN_MEMO[key] = "built once"
    assert otools._RUN_MEMO.get(_memo_key("engineer", {"company_name": "acme"})) == "built once"


def test_clearing_the_memo_allows_a_fresh_build():
    # A new Research chain means a genuinely new run; the previous run's
    # results must not be served into it.
    otools._RUN_MEMO[_memo_key("engineer", {"company_name": "Acme"})] = "old"
    otools._RUN_MEMO.clear()
    assert otools._RUN_MEMO.get(_memo_key("engineer", {"company_name": "Acme"})) is None
