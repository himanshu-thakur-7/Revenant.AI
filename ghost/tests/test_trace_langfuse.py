"""T0 coverage for ghost/trace_langfuse.py's pure logic — masking,
trace-dimension derivation, and observation-type mapping. No network: the
Langfuse client is never constructed here.

These follow the Langfuse instrumentation baseline (the `langfuse` skill's
references/instrumentation.md + langfuse.com/docs/observability/best-practices):
sensitive data masked, most-specific observation types, and trace-level
user_id/session_id/tags for per-customer analytics.
"""

from __future__ import annotations

from ghost.trace_langfuse import _KIND_TO_AS_TYPE, _mask, _trace_dims

# ── masking (the "sensitive data masked" baseline requirement) ─────────


def test_masks_email_addresses():
    out = _mask({"recipient": "dzhou@plaid.com"})
    assert "dzhou@plaid.com" not in out
    assert "<email-redacted>" in out


def test_masks_api_key_shapes():
    for secret in ("sk-lf-abcdef123456", "pk-lf-abcdef123456",
                   "rzp_live_abcdef1234", "ghp_abcdefghijklmnop"):
        out = _mask({"k": secret})
        assert secret not in out, f"{secret} survived masking"


def test_masks_bearer_and_authorization_pairs():
    out = _mask("Authorization: Bearer abc123xyz")
    assert "abc123xyz" not in out


def test_masks_inside_plain_strings_not_just_dicts():
    out = _mask("mail me at founder@acme.com please")
    assert "founder@acme.com" not in out


def test_masking_preserves_non_sensitive_content():
    out = _mask({"merchant": "PhonePe", "pain": "UPI reconciliation"})
    assert "PhonePe" in out and "UPI reconciliation" in out


def test_masking_never_raises_on_unserializable_input():
    class Boom:
        def __repr__(self):        # json.dumps(default=str) will call this
            raise RuntimeError("nope")

    out = _mask({"x": Boom()})
    assert isinstance(out, str)     # returns a redacted placeholder, not a raise


# ── trace dimensions (per-customer analytics) ─────────────────────────


def test_dims_map_startup_to_user_id():
    dims = _trace_dims({"attrs": {"startup": "Razorpay"}})
    assert dims["user_id"] == "razorpay"


def test_dims_group_a_campaign_into_one_session():
    dims = _trace_dims({"attrs": {"startup": "Razorpay", "merchant": "PhonePe"}})
    assert dims["session_id"] == "razorpay:phonepe"


def test_dims_have_no_session_without_a_merchant():
    # A prototype-only call has no campaign to group yet.
    dims = _trace_dims({"attrs": {"startup": "Razorpay"}})
    assert dims["session_id"] is None


def test_dims_include_filterable_tags():
    dims = _trace_dims({
        "attrs": {"startup": "Razorpay"},
        "revenant.mode": "live", "revenant.env": "mcp",
    })
    assert "tenant:razorpay" in dims["tags"]
    assert "mode:live" in dims["tags"]


def test_dims_are_empty_for_an_unattributed_span():
    # A bare ghost-pipeline span carries no startup; it must still record,
    # just without customer dimensions.
    dims = _trace_dims({"attrs": {}})
    assert dims["user_id"] is None
    assert dims["session_id"] is None


def test_dims_tenant_matches_tenancy_resolution():
    from agents import tenancy

    dims = _trace_dims({"attrs": {"startup": "Bombay Shaving Company"}})
    assert dims["user_id"] == tenancy.slug("Bombay Shaving Company")


# ── observation types (drives the Agent Graph + per-type analytics) ────


def test_llm_calls_are_generations():
    assert _KIND_TO_AS_TYPE["llm"] == "generation"
    assert _KIND_TO_AS_TYPE["llm.vision"] == "generation"


def test_wrapper_is_a_chain_not_a_second_generation():
    # Counting a delegating wrapper as a generation would double-count
    # tokens/cost for every wrapped call.
    assert _KIND_TO_AS_TYPE["llm.wrapper"] == "chain"


def test_specific_types_are_used_instead_of_generic_spans():
    assert _KIND_TO_AS_TYPE["mcp"] == "agent"
    assert _KIND_TO_AS_TYPE["context"] == "retriever"
    assert _KIND_TO_AS_TYPE["eval"] == "evaluator"
    assert _KIND_TO_AS_TYPE["judge"] == "evaluator"


def test_every_mapped_type_is_a_real_langfuse_type():
    valid = {"event", "span", "generation", "agent", "tool", "chain",
             "retriever", "evaluator", "embedding", "guardrail"}
    unknown = {k: v for k, v in _KIND_TO_AS_TYPE.items() if v not in valid}
    assert not unknown, f"not real Langfuse observation types: {unknown}"
