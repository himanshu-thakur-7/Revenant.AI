"""T0 (contract/unit) coverage for evals/ — pure logic only, no network,
no LLM calls. See docs/evals-observability-design.md §1.1 for the tier
definitions; T1+ (deterministic artifact checks, LLM judge, live e2e) are
NOT here — they hit real URLs/files by design and run via `make eval`,
not `pytest -q`.
"""

from __future__ import annotations

import json

from evals.bundle import Bundle, from_disk, merge_into, slug


def test_slug_matches_engineer_prototype_slug():
    # evals/bundle.py duplicates this logic instead of importing it, purely
    # to keep evals/'s own import graph independent of agents/engineer's —
    # this test is what actually keeps the two functions in sync.
    from agents.engineer.prototype import _slug as engineer_slug

    for name in ["Meesho", "Bombay Shaving Company", "1mg", "  weird--Name!! ", ""]:
        assert slug(name) == engineer_slug(name)


def test_bundle_round_trips(tmp_path, monkeypatch):
    import evals.bundle as bundle_mod

    monkeypatch.setattr(bundle_mod, "BUNDLES_DIR", tmp_path)
    b = Bundle(bundle_id="t1", created_at="now", git_sha="abc123",
               startup="Razorpay", merchant="Acme")
    b.save()
    loaded = Bundle.load("t1")
    assert loaded == b


def test_merge_into_updates_dicts_not_clobbers(tmp_path, monkeypatch):
    import evals.bundle as bundle_mod

    monkeypatch.setattr(bundle_mod, "BUNDLES_DIR", tmp_path)
    bid = "merge-test"
    b1 = merge_into(bid, startup="Razorpay", merchant="Acme",
                     prototype_url="https://x/p/", models={"engineer": "gpt-4.1"})
    assert b1.prototype_url == "https://x/p/"

    b2 = merge_into(bid, deck_url="https://x/d.pptx", models={"sales": "gpt-4o"})
    assert b2.prototype_url == "https://x/p/"          # not clobbered
    assert b2.deck_url == "https://x/d.pptx"
    assert b2.models == {"engineer": "gpt-4.1", "sales": "gpt-4o"}   # merged

    b3 = merge_into(bid, prototype_url="")              # empty patch must not overwrite
    assert b3.prototype_url == "https://x/p/"


def test_merge_into_ignores_unknown_fields(tmp_path, monkeypatch):
    import evals.bundle as bundle_mod

    monkeypatch.setattr(bundle_mod, "BUNDLES_DIR", tmp_path)
    b = merge_into("unk-test", startup="Razorpay", merchant="Acme",
                    totally_made_up_field="should be dropped, not crash")
    assert not hasattr(b, "totally_made_up_field")


def test_artifacts_reports_claimed_kinds():
    b = Bundle(bundle_id="t", created_at="now", git_sha="x",
               prototype_url="https://x/p/")
    a = b.artifacts()
    assert a["prototype"] is True
    assert a["walkthrough"] is False
    assert a["deck"] is False
    assert a["email"] is False


def test_from_disk_no_artifacts_is_all_false():
    b = from_disk("NoSuchMerchantAtAllXYZ123")
    assert not any(b.artifacts().values())


def test_bundle_new_id_is_slug_based():
    bid = Bundle.new_id("Bombay Shaving Company")
    assert bid.endswith("bombay-shaving-company")
