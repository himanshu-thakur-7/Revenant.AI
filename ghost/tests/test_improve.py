"""T0 coverage for evals/improve.py::propose_patch() — pure clustering
logic over a synthetic history.jsonl, no LLM, no network. See
docs/evals-observability-design.md §3.3.
"""

from __future__ import annotations

import json

import evals.history as history_mod
import evals.improve as improve_mod


def _write_history(tmp_path, entries):
    p = tmp_path / "history.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    return p


def _entry(bundle_id, reasons, kind="prototype"):
    return {
        "recorded_at": "2026-01-01T00:00:00",
        "bundle_id": bundle_id,
        "git_sha": "abc123",
        "merchant": "Acme",
        "prompt_versions": {},
        "artifacts": {kind: {"t1_pass": True, "judge_composite": 40,
                             "fail_reasons": reasons}},
        "bundle_pass": False,
    }


def test_no_history_is_empty_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(history_mod, "HISTORY_PATH", tmp_path / "nope.jsonl")
    assert improve_mod.propose_patch() == []


def test_below_threshold_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(history_mod, "HISTORY_PATH", _write_history(tmp_path, [
        _entry("b1", ["Demo doesn't update when the button is clicked."]),
        _entry("b2", ["Demo doesn't update when the button is clicked."]),
    ]))
    monkeypatch.setattr(improve_mod, "PROPOSALS_DIR", tmp_path / "proposals")
    assert improve_mod.propose_patch() == []


def test_recurring_failure_crosses_threshold(tmp_path, monkeypatch):
    same_reason = "Demo doesn't update when the button is clicked."
    entries = [_entry(f"b{i}", [same_reason]) for i in range(3)]
    entries.append(_entry("b4", ["Something unrelated entirely."]))
    monkeypatch.setattr(history_mod, "HISTORY_PATH", _write_history(tmp_path, entries))
    proposals_dir = tmp_path / "proposals"
    monkeypatch.setattr(improve_mod, "PROPOSALS_DIR", proposals_dir)

    written = improve_mod.propose_patch()
    assert len(written) == 1
    text = written[0].read_text()
    assert "b0" in text and "b1" in text and "b2" in text
    assert "b4" not in text  # the unrelated one-off must not appear in THIS cluster
    assert "demo" in written[0].name  # kind is in the filename
    assert "PROPOSAL ONLY" in text
    assert "Suggested diff" in text
    # never touches a real prompt file
    assert "agents/engineer/planner.py" in text  # target identified...
    for p in tmp_path.rglob("*"):
        assert p.name not in ("planner.py", "prompt.py")  # ...but nothing WRITTEN to one


def test_normalization_merges_case_and_whitespace_variants(tmp_path, monkeypatch):
    entries = [
        _entry("b1", ["Demo doesn't update on click"]),
        _entry("b2", ["  demo doesn't update on click  "]),
        _entry("b3", ["DEMO DOESN'T UPDATE ON CLICK."]),
    ]
    monkeypatch.setattr(history_mod, "HISTORY_PATH", _write_history(tmp_path, entries))
    monkeypatch.setattr(improve_mod, "PROPOSALS_DIR", tmp_path / "proposals")

    written = improve_mod.propose_patch()
    assert len(written) == 1  # all three collapse into one cluster


def test_different_kinds_never_cluster_together(tmp_path, monkeypatch):
    proto_reason = "not specific enough to the merchant"
    email_reason = "doesn't ground its claims in real evidence"
    entries = [_entry(f"b{i}", [proto_reason], kind="prototype") for i in range(3)]
    entries += [_entry(f"c{i}", [email_reason], kind="email") for i in range(3)]
    monkeypatch.setattr(history_mod, "HISTORY_PATH", _write_history(tmp_path, entries))
    monkeypatch.setattr(improve_mod, "PROPOSALS_DIR", tmp_path / "proposals")

    written = improve_mod.propose_patch()
    assert len(written) == 2
    # two distinct proposal files, one per kind, each names its target
    texts = [p.read_text() for p in written]
    assert any("agents/engineer/planner.py" in t for t in texts)
    assert any("agents/sales/prompt.py" in t for t in texts)


def test_unmapped_reason_flags_manual_triage(tmp_path, monkeypatch):
    reason = "completely novel failure mode nobody anticipated"
    entries = [_entry(f"b{i}", [reason]) for i in range(3)]
    monkeypatch.setattr(history_mod, "HISTORY_PATH", _write_history(tmp_path, entries))
    monkeypatch.setattr(improve_mod, "PROPOSALS_DIR", tmp_path / "proposals")

    written = improve_mod.propose_patch()
    assert len(written) == 1
    assert "manual triage" in written[0].read_text()
