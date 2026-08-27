"""agents/base.py — parsing the LLM's tool calls out of a raw message.

Every agent's ability to DO anything routes through here. When this
fails, the model's intent is silently discarded: the agent looks like it
"decided not to call the tool" when in fact the call was emitted and
dropped. That is indistinguishable from a reasoning failure from the
outside, which makes it exactly the kind of bug that gets misattributed.

The repair path exists for a real Nous Hermes-4 quirk: it emits a nested
object literal as a string parameter WITHOUT escaping it, producing
invalid JSON that a strict parser rejects outright.

Offline: pure string parsing, no LLM.
"""

from __future__ import annotations

import json

from agents.base import (
    _extract_balanced_braces, _extract_inline_tool_calls,
    _repair_nous_args, _strip_inline_tool_calls,
)


def block(inner: str) -> str:
    return f"<tool_call>{inner}</tool_call>"


# ── balanced braces ───────────────────────────────────────────────────

def test_a_simple_object_is_spanned():
    s = '{"a":1}'
    assert _extract_balanced_braces(s, 0) == (0, len(s) - 1)


def test_nested_objects_are_spanned_to_the_outer_close():
    s = '{"a":{"b":{"c":1}}}'
    assert _extract_balanced_braces(s, 0) == (0, len(s) - 1)


def test_trailing_content_after_the_object_is_excluded():
    s = '{"a":1}, "next": 2'
    assert _extract_balanced_braces(s, 0) == (0, 6)


def test_an_unbalanced_object_returns_none():
    # Truncated generation — must report "cannot parse", not guess a span.
    assert _extract_balanced_braces('{"a":{"b":1}', 0) is None


def test_a_non_brace_start_returns_none():
    assert _extract_balanced_braces('"a":1', 0) is None


def test_an_out_of_range_start_returns_none():
    assert _extract_balanced_braces("{}", 99) is None


def test_an_empty_string_returns_none():
    assert _extract_balanced_braces("", 0) is None


# ── the Nous repair path ──────────────────────────────────────────────

def test_an_unescaped_nested_object_is_recovered():
    # THE quirk: prospect_json opens a quoted string and then emits a raw
    # object literal inside it, never closing the quote. Strict JSON dies.
    raw = '{"prospect_json": "{"company_name": "Acme", "domain": "acme.com"}"}'
    got = _repair_nous_args(raw)
    assert got and "prospect_json" in got
    assert "Acme" in got["prospect_json"]


def test_a_bare_object_literal_is_also_recovered():
    raw = '{"prospect_json": {"company_name": "Acme"}}'
    got = _repair_nous_args(raw)
    assert got and "Acme" in got["prospect_json"]


def test_plain_string_arguments_are_extracted():
    raw = '{"prototype_url": "https://x.test/acme/", "brief": "fintech"}'
    got = _repair_nous_args(raw)
    assert got["prototype_url"] == "https://x.test/acme/"
    assert got["brief"] == "fintech"


def test_escaped_quotes_inside_a_string_argument_survive():
    raw = r'{"brief": "the \"big\" one"}'
    assert r"\"big\"" in _repair_nous_args(raw)["brief"]


def test_numeric_arguments_are_typed_not_stringified():
    got = _repair_nous_args('{"max_prospects": 3}')
    assert got["max_prospects"] == 3
    assert isinstance(got["max_prospects"], int)


def test_a_negative_number_is_handled():
    assert _repair_nous_args('{"max_prospects": -1}')["max_prospects"] == -1


def test_nothing_recognisable_returns_none():
    # None (not {}) so the caller can distinguish "no args found" from
    # "an empty argument object was intended".
    assert _repair_nous_args('{"totally_unknown": "x"}') is None


def test_several_argument_kinds_recover_together():
    raw = ('{"prospect_json": "{"company_name": "Acme"}", '
           '"prototype_url": "https://x.test/", "max_prospects": 2}')
    got = _repair_nous_args(raw)
    assert set(got) >= {"prospect_json", "prototype_url", "max_prospects"}


# ── inline <tool_call> extraction ─────────────────────────────────────

def test_a_well_formed_call_is_parsed():
    calls = _extract_inline_tool_calls(
        block('{"name": "build_prototype", "arguments": {"merchant": "Acme"}}'))
    assert len(calls) == 1
    assert calls[0].function.name == "build_prototype"
    assert json.loads(calls[0].function.arguments)["merchant"] == "Acme"


def test_parameters_is_accepted_as_an_alias_for_arguments():
    calls = _extract_inline_tool_calls(
        block('{"name": "t", "parameters": {"a": 1}}'))
    assert json.loads(calls[0].function.arguments)["a"] == 1


def test_string_arguments_are_passed_through_unchanged():
    calls = _extract_inline_tool_calls(
        block('{"name": "t", "arguments": "{\\"a\\": 1}"}'))
    assert json.loads(calls[0].function.arguments)["a"] == 1


def test_multiple_calls_in_one_message_are_all_returned():
    text = block('{"name":"a","arguments":{}}') + "\n" + block('{"name":"b","arguments":{}}')
    assert [c.function.name for c in _extract_inline_tool_calls(text)] == ["a", "b"]


def test_each_call_gets_a_unique_id():
    text = block('{"name":"a","arguments":{}}') + block('{"name":"a","arguments":{}}')
    ids = [c.id for c in _extract_inline_tool_calls(text)]
    assert len(set(ids)) == 2


def test_the_broken_nous_shape_still_yields_a_call():
    # End to end through the repair path: strict parse fails, the tool
    # call is still recovered rather than the model's intent being lost.
    calls = _extract_inline_tool_calls(block(
        '{"name": "build_campaign", "arguments": '
        '{"prospect_json": "{"company_name": "Acme"}"}}'))
    assert len(calls) == 1
    assert calls[0].function.name == "build_campaign"
    assert "Acme" in calls[0].function.arguments


def test_text_with_no_tool_call_yields_nothing():
    assert _extract_inline_tool_calls("just a normal reply") == []
    assert _extract_inline_tool_calls("") == []
    assert _extract_inline_tool_calls(None) == []


def test_a_call_without_a_name_is_skipped_not_fatal():
    assert _extract_inline_tool_calls(block('{"arguments": {"a": 1}}')) == []


def test_unrepairable_arguments_skip_that_call_only():
    good = block('{"name":"ok","arguments":{}}')
    bad = block('{"name": "broken", "arguments": nonsense')
    names = [c.function.name for c in _extract_inline_tool_calls(bad + good)]
    assert names == ["ok"]


def test_a_call_missing_its_arguments_key_is_skipped_on_the_repair_path():
    assert _extract_inline_tool_calls(block('{"name": "t", "oops": {')) == []


# ── stripping, so the block is not recorded twice ─────────────────────

def test_tool_call_blocks_are_removed_from_the_visible_text():
    text = "Building now.\n" + block('{"name":"t","arguments":{}}')
    out = _strip_inline_tool_calls(text)
    assert "<tool_call>" not in out
    assert "Building now." in out


def test_stripping_removes_every_block():
    text = block('{"name":"a"}') + "mid" + block('{"name":"b"}')
    assert "<tool_call>" not in _strip_inline_tool_calls(text)


def test_stripping_handles_empty_input():
    assert _strip_inline_tool_calls("") == ""
    assert _strip_inline_tool_calls(None) == ""


def test_text_without_blocks_is_returned_trimmed():
    assert _strip_inline_tool_calls("  hello  ") == "hello"
