"""T0 coverage for evals/runner.py's pure gating logic -- artifact_pass,
bundle_pass, score_summary's T1/T2 gate contract -- against hand-built
Check/JudgeResult objects, no real checks or network involved. This is
the actual PASS/FAIL contract the whole framework promises
(docs/evals-observability-design.md §1.7: "all T1 PASS and (composite >=
70 OR not judged)") and had zero direct tests despite runner.py showing
up in zero test files before now.
"""

from __future__ import annotations

from evals.checks import Check
from evals.judge import CriterionScore, JudgeResult
from evals.runner import artifact_pass, bundle_pass


def test_artifact_pass_all_true():
    assert artifact_pass([Check("a", True), Check("b", True)])


def test_artifact_pass_one_false_fails_the_whole_group():
    assert not artifact_pass([Check("a", True), Check("b", False)])


def test_artifact_pass_empty_list_is_vacuously_true():
    assert artifact_pass([])


def test_bundle_pass_all_t1_pass_no_judge():
    scored = {"prototype": {"checks": [Check("x", True)], "t1_pass": True, "judge": None}}
    assert bundle_pass(scored)


def test_bundle_pass_t1_fail_fails_bundle_even_with_perfect_judge():
    # The hard-gate contract: a T1 FAIL fails the bundle regardless of what
    # the judge would have said -- and here the judge is never even asked.
    scored = {"prototype": {"checks": [Check("x", False)], "t1_pass": False, "judge": None}}
    assert not bundle_pass(scored)


def test_bundle_pass_judge_at_exactly_70_passes():
    # composite 70.0 is the documented >= boundary, not a > boundary.
    # build an exact-70 composite via two criteria: 60 + 10
    a = CriterionScore(criterion="a", weight=60, raw_score=4, verified=True, evidence=["e"])
    b = CriterionScore(criterion="b", weight=40, raw_score=1, verified=True, evidence=["e"])
    judge = JudgeResult(artifact_kind="email", criteria=[a, b])
    assert judge.composite == 70.0
    scored = {"email": {"checks": [Check("x", True)], "t1_pass": True, "judge": judge}}
    assert bundle_pass(scored)


def test_bundle_pass_judge_just_under_70_fails():
    a = CriterionScore(criterion="a", weight=60, raw_score=4, verified=True, evidence=["e"])
    b = CriterionScore(criterion="b", weight=40, raw_score=0, verified=True, evidence=["e"])
    judge = JudgeResult(artifact_kind="email", criteria=[a, b])
    assert judge.composite == 60.0
    scored = {"email": {"checks": [Check("x", True)], "t1_pass": True, "judge": judge}}
    assert not bundle_pass(scored)


def test_bundle_pass_missing_optional_artifact_does_not_fail_bundle():
    # A bundle that only claims a prototype (no walkthrough built yet)
    # isn't penalized for a kind that's simply absent from `scored`.
    scored = {"prototype": {"checks": [Check("x", True)], "t1_pass": True, "judge": None}}
    assert bundle_pass(scored)
    assert "walkthrough" not in scored  # sanity: absence, not a failing entry


def test_bundle_pass_multiple_artifacts_all_must_pass():
    scored = {
        "prototype": {"checks": [Check("x", True)], "t1_pass": True, "judge": None},
        "deck": {"checks": [Check("y", False)], "t1_pass": False, "judge": None},
    }
    assert not bundle_pass(scored)


def test_unverified_citation_forces_score_zero_and_fails_bundle():
    # M3 in evals/judge.py's own docstring: an unverified citation forces
    # that criterion's effective score to 0 regardless of raw_score.
    c = CriterionScore(criterion="only", weight=100, raw_score=4, verified=False, evidence=[])
    judge = JudgeResult(artifact_kind="email", criteria=[c])
    assert judge.composite == 0.0
    scored = {"email": {"checks": [Check("x", True)], "t1_pass": True, "judge": judge}}
    assert not bundle_pass(scored)
