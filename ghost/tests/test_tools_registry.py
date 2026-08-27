"""agents/tools.py — the @tool decorator, schema derivation, and dispatch.

Foundational: every agent's entire capability surface is generated here.
A wrong schema means the LLM is told the wrong thing about how to call a
tool, and a fragile dispatch path means a malformed model response takes
an agent down mid-campaign instead of producing a correctable error.

The dispatch tests deliberately push malformed input, because the input
comes from an LLM and is therefore untrusted by definition.
"""

from __future__ import annotations

from typing import Optional

from agents.tools import Tool, tool


# ── schema derivation ─────────────────────────────────────────────────

def test_primitive_hints_map_to_json_types():
    @tool("t")
    def f(a: str, b: int, c: float, d: bool):
        return ""

    p = f.parameters
    assert p["a"] == {"type": "string"}
    assert p["b"] == {"type": "integer"}
    assert p["c"] == {"type": "number"}
    assert p["d"] == {"type": "boolean"}


def test_params_without_defaults_are_required():
    @tool("t")
    def f(needed: str, optional: str = "x"):
        return ""

    assert f.required == ["needed"]


def test_a_tool_with_no_params_has_an_empty_schema():
    @tool("t")
    def f():
        return ""

    assert f.parameters == {}
    assert f.required == []


def test_optional_unwraps_to_the_inner_type():
    @tool("t")
    def f(a: Optional[int] = None):
        return ""

    assert f.parameters["a"] == {"type": "integer"}


def test_list_hints_become_arrays_with_item_types():
    @tool("t")
    def f(xs: list[str], ns: list[int]):
        return ""

    assert f.parameters["xs"] == {"type": "array", "items": {"type": "string"}}
    assert f.parameters["ns"]["items"] == {"type": "integer"}


def test_dict_hints_become_objects():
    @tool("t")
    def f(d: dict, dd: dict[str, int]):
        return ""

    # REGRESSION: a BARE `dict` used to fall through to {"type":"string"},
    # because get_origin(dict) is None — only dict[str, Any] has an origin.
    # The LLM was then told to send a string to a parameter expecting an
    # object: a wrong schema, not just an imprecise one.
    assert f.parameters["d"] == {"type": "object"}
    assert f.parameters["dd"] == {"type": "object"}


def test_bare_list_hints_become_arrays():
    @tool("t")
    def f(xs: list):
        return ""

    assert f.parameters["xs"]["type"] == "array"


def test_an_unhintable_param_falls_back_to_string():
    # Better to tell the LLM "string" than to emit no type at all — it will
    # send something, and a string is the one shape every value survives.
    @tool("t")
    def f(weird: complex):
        return ""

    assert f.parameters["weird"] == {"type": "string"}


def test_an_unannotated_param_defaults_to_string():
    @tool("t")
    def f(bare):
        return ""

    assert f.parameters["bare"] == {"type": "string"}


def test_the_name_comes_from_the_function():
    @tool("t")
    def read_prospect_brief():
        return ""

    assert read_prospect_brief.name == "read_prospect_brief"


def test_description_is_trimmed():
    @tool("  spaced out  ")
    def f():
        return ""

    assert f.description == "spaced out"


def test_schema_is_openai_function_shape():
    @tool("does a thing")
    def f(a: str, b: int = 1):
        return ""

    s = f.schema()
    assert s["type"] == "function"
    assert s["function"]["name"] == "f"
    assert s["function"]["description"] == "does a thing"
    assert s["function"]["parameters"]["type"] == "object"
    assert s["function"]["parameters"]["required"] == ["a"]
    assert set(s["function"]["parameters"]["properties"]) == {"a", "b"}


# ── dispatch: arguments arrive from an LLM, so treat them as hostile ───

def _echo_tool():
    @tool("echo")
    def echo(msg: str = "", n: int = 1):
        return f"{msg}x{n}"
    return echo


def test_a_normal_call_works():
    assert _echo_tool().call('{"msg":"hi","n":2}') == "hix2"


def test_empty_arguments_use_defaults():
    assert _echo_tool().call("") == "x1"
    assert _echo_tool().call("{}") == "x1"


def test_invalid_json_returns_an_error_string_not_a_raise():
    # An exception here would kill the agent's turn; a string is something
    # the model can read and correct on the next step.
    out = _echo_tool().call("{not json")
    assert out.startswith("[tool-error]")


def test_double_encoded_arguments_are_unwrapped():
    # A real live-LLM quirk: arguments arrive as a JSON string whose value
    # is itself a JSON object.
    assert _echo_tool().call('"{\\"msg\\":\\"hi\\",\\"n\\":3}"') == "hix3"


def test_a_json_array_is_rejected_clearly():
    out = _echo_tool().call('["not","an","object"]')
    assert out.startswith("[tool-error]")
    assert "expected a JSON object" in out


def test_a_bare_json_scalar_is_rejected_clearly():
    assert _echo_tool().call("42").startswith("[tool-error]")


def test_an_unexpected_keyword_is_reported_not_raised():
    out = _echo_tool().call('{"msg":"hi","nope":1}')
    assert out.startswith("[tool-error]")
    assert "echo" in out


def test_a_missing_required_argument_is_reported():
    @tool("needs one")
    def needs(a: str):
        return a

    assert needs.call("{}").startswith("[tool-error]")


def test_an_exception_inside_the_tool_becomes_an_error_string():
    @tool("boom")
    def boom():
        raise ValueError("kaboom")

    out = boom.call("{}")
    assert out.startswith("[tool-error]")
    assert "ValueError" in out and "kaboom" in out


def test_a_non_string_result_is_json_encoded():
    @tool("obj")
    def obj():
        return {"a": 1, "b": [2, 3]}

    assert obj.call("{}") == '{"a": 1, "b": [2, 3]}'


def test_an_unserialisable_result_still_returns_a_string():
    class Weird:
        def __repr__(self):
            return "<weird>"

    @tool("weird")
    def weird():
        return {"k": Weird()}

    out = weird.call("{}")
    assert isinstance(out, str) and "weird" in out


def test_a_string_result_is_passed_through_unchanged():
    @tool("s")
    def s():
        return "plain text"

    assert s.call("{}") == "plain text"


def test_the_decorator_returns_a_Tool_not_a_function():
    @tool("t")
    def f():
        return ""

    assert isinstance(f, Tool)
