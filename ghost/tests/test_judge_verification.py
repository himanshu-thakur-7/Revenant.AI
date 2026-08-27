"""evals/judge.py — citation verification and score composition.

This is the mechanism that stops the eval framework fooling itself. An
LLM judge that hallucinates evidence is worse than no judge: it produces
confident numbers with nothing behind them, and those numbers then gate
what a founder ships and feed the improvement loop.

The rule (judge.py's M3): every criterion score must cite exact
substrings from the REAL artifact. A citation that does not literally
appear forces that criterion to zero, whatever the judge claimed.

Offline: no LLM call — these test the verification and arithmetic.
"""

from __future__ import annotations

import pytest

from evals.judge import (
    RUBRICS, CriterionScore, JudgeResult, _normalize, _verify_citations,
)

ARTIFACT = """<html><body>
<h1>PhonePe UPI reconciliation</h1>
<div id="demo"><input id="demoInput" value="settlement_2026_08.csv"></div>
<p>Match settlement files against the ledger automatically.</p>
</body></html>"""


# ── citation verification (M3) ────────────────────────────────────────

def test_a_real_quote_verifies():
    assert _verify_citations(["Match settlement files"], ARTIFACT)


def test_a_fabricated_quote_does_not_verify():
    # The judge inventing plausible-sounding evidence is the exact failure
    # this whole mechanism exists to make impossible.
    assert not _verify_citations(["Real-time fraud scoring engine"], ARTIFACT)


def test_all_citations_must_verify_not_just_one():
    # One real quote must not launder a fabricated one alongside it.
    assert not _verify_citations(
        ["Match settlement files", "Real-time fraud scoring"], ARTIFACT)


def test_an_empty_evidence_list_never_verifies():
    # A score with no proof is not a score.
    assert not _verify_citations([], ARTIFACT)


def test_verification_tolerates_whitespace_and_case():
    # Reformatting is not fabrication; the judge may re-wrap a quote.
    assert _verify_citations(["MATCH   SETTLEMENT\n  FILES"], ARTIFACT)


def test_verification_is_not_fuzzy():
    # Paraphrase is precisely how an invented citation would sneak in, so
    # matching must stay substring-exact after normalisation.
    assert not _verify_citations(["matches settlement file"], ARTIFACT)


def test_blank_entries_are_ignored_not_treated_as_proof():
    assert _verify_citations(["Match settlement files", ""], ARTIFACT)


def test_normalisation_collapses_whitespace_and_folds_case():
    assert _normalize("  A   B\n\tC ") == "a b c"
    assert _normalize(None) == ""


# ── the forced-zero rule ──────────────────────────────────────────────

def test_an_unverified_criterion_scores_zero_however_high_the_claim():
    c = CriterionScore(criterion="x", weight=30, raw_score=4, verified=False,
                       evidence=["invented"])
    assert c.raw_score == 4        # what the judge claimed is preserved…
    assert c.score == 0            # …and does not count
    assert c.weighted == 0.0


def test_a_verified_criterion_keeps_its_score():
    c = CriterionScore(criterion="x", weight=30, raw_score=3, verified=True,
                       evidence=["real"])
    assert c.score == 3
    assert c.weighted == pytest.approx(22.5)   # 3/4 * 30


def test_weighting_is_proportional_to_the_rubric_weight():
    a = CriterionScore(criterion="a", weight=10, raw_score=4, verified=True, evidence=["e"])
    b = CriterionScore(criterion="b", weight=40, raw_score=4, verified=True, evidence=["e"])
    assert b.weighted == pytest.approx(4 * a.weighted)


# ── composite arithmetic ──────────────────────────────────────────────

def _r(*criteria, kind="prototype"):
    return JudgeResult(artifact_kind=kind, criteria=list(criteria))


def test_a_perfect_verified_result_is_100():
    crits = [CriterionScore(criterion=n, weight=w, raw_score=4, verified=True,
                            evidence=["e"])
             for n, w in RUBRICS["prototype"].items()]
    assert _r(*crits).composite == pytest.approx(100.0)


def test_an_entirely_unverified_result_is_zero():
    crits = [CriterionScore(criterion=n, weight=w, raw_score=4, verified=False)
             for n, w in RUBRICS["prototype"].items()]
    assert _r(*crits).composite == 0.0


def test_one_fabricated_criterion_costs_exactly_its_weight():
    ok = CriterionScore(criterion="a", weight=60, raw_score=4, verified=True, evidence=["e"])
    bad = CriterionScore(criterion="b", weight=40, raw_score=4, verified=False)
    assert _r(ok, bad).composite == pytest.approx(60.0)


def test_the_composite_is_rounded_for_reporting():
    c = CriterionScore(criterion="a", weight=33, raw_score=1, verified=True, evidence=["e"])
    assert _r(c).composite == round(_r(c).composite, 1)


def test_an_empty_result_is_zero_not_an_error():
    assert _r().composite == 0.0


# ── the human-readable summary ────────────────────────────────────────

def test_the_summary_flags_an_unverified_citation_loudly():
    # A reader must be able to tell a genuine 0 from a rejected claim.
    c = CriterionScore(criterion="account_specificity", weight=30, raw_score=4,
                       verified=False, evidence=["invented"])
    assert "UNVERIFIED_CITATION" in str(_r(c))


def test_the_summary_reports_the_composite_and_each_criterion():
    c = CriterionScore(criterion="demo_realism", weight=25, raw_score=2,
                       verified=True, evidence=["e"], fail_reasons=["no live data"])
    s = str(_r(c))
    assert "composite:" in s
    assert "demo_realism" in s
    assert "no live data" in s


def test_instability_is_surfaced_in_the_summary():
    # n=2 judging that disagrees with itself must not read as a settled score.
    c = CriterionScore(criterion="a", weight=10, raw_score=2, verified=True, evidence=["e"])
    assert "UNSTABLE" in str(JudgeResult(artifact_kind="prototype", criteria=[c],
                                         unstable=True))


# ── rubric integrity ──────────────────────────────────────────────────

@pytest.mark.parametrize("kind", list(RUBRICS))
def test_every_rubric_weights_to_100(kind):
    # If a rubric does not sum to 100 the composite silently stops being a
    # percentage, and every threshold built on it (the >=70 gate) shifts.
    assert sum(RUBRICS[kind].values()) == 100


@pytest.mark.parametrize("kind", list(RUBRICS))
def test_no_rubric_has_a_zero_or_negative_weight(kind):
    assert all(w > 0 for w in RUBRICS[kind].values())


def test_the_judged_kinds_have_rubrics():
    from evals.runner import _JUDGEABLE_KINDS
    assert _JUDGEABLE_KINDS <= set(RUBRICS)
