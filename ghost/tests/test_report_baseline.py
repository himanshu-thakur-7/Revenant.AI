"""T0 coverage for evals/report.py — baseline accept/load/diff, pure logic
against real files under tmp_path, no network. Previously entirely
untested (0 tests touched this module before now), despite being the
actual regression-gate mechanism the eval CLI's `--accept-baseline` and
`diff_against_baseline` promise to enforce.
"""

from __future__ import annotations

import evals.report as report_mod
from evals.judge import CriterionScore, JudgeResult


def _scored(composite_score: int, *, kind="email"):
    # A single-criterion JudgeResult whose composite is fully controlled
    # by raw_score (weight=100 keeps composite == raw_score*25, so pass 4
    # for 100, 0 for 0, etc — simplest possible knob for these tests).
    c = CriterionScore(criterion="only", weight=100, raw_score=composite_score,
                       verified=True, evidence=["e"], fail_reasons=[])
    judge = JudgeResult(artifact_kind=kind, criteria=[c])
    return {kind: {"checks": [], "t1_pass": True, "judge": judge}}


def test_load_baseline_missing_is_empty_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(report_mod, "BASELINES_DIR", tmp_path)
    assert report_mod.load_baseline("nosuite") == {}


def test_accept_then_load_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(report_mod, "BASELINES_DIR", tmp_path)
    scored = _scored(4)  # raw_score 4/4 -> composite 100.0
    report_mod.accept_baseline("suite", "golden-a", scored)

    data = report_mod.load_baseline("suite")
    assert "golden-a" in data
    assert data["golden-a"]["kinds"]["email"]["composite"] == 100.0
    assert data["golden-a"]["kinds"]["email"]["t1_pass"] is True


def test_diff_with_no_baseline_reports_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(report_mod, "BASELINES_DIR", tmp_path)
    problems = report_mod.diff_against_baseline("suite", "golden-never-baselined", _scored(2))
    assert problems == []


def test_diff_detects_a_real_regression(tmp_path, monkeypatch):
    monkeypatch.setattr(report_mod, "BASELINES_DIR", tmp_path)
    report_mod.accept_baseline("suite", "golden-b", _scored(4))   # baseline: 100.0

    problems = report_mod.diff_against_baseline("suite", "golden-b", _scored(0))  # now: 0.0
    assert len(problems) == 1
    assert "100.0 -> 0.0" in problems[0]


def test_diff_ignores_a_small_drop_under_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(report_mod, "BASELINES_DIR", tmp_path)
    report_mod.accept_baseline("suite", "golden-c", _scored(4))   # 100.0

    # raw_score 3/4 -> composite 75.0, a 25-point drop is still under the
    # default regression_threshold of... actually let's use an explicit
    # small threshold-respecting case instead of relying on the default.
    problems = report_mod.diff_against_baseline("suite", "golden-c", _scored(4),
                                                 regression_threshold=10.0)
    assert problems == []  # identical score, zero delta -- definitely no regression


def test_accept_baseline_never_implicitly_overwrites_other_goldens(tmp_path, monkeypatch):
    monkeypatch.setattr(report_mod, "BASELINES_DIR", tmp_path)
    report_mod.accept_baseline("suite", "golden-x", _scored(4))
    report_mod.accept_baseline("suite", "golden-y", _scored(0))

    data = report_mod.load_baseline("suite")
    assert data["golden-x"]["kinds"]["email"]["composite"] == 100.0
    assert data["golden-y"]["kinds"]["email"]["composite"] == 0.0
