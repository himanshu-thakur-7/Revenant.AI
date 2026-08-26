"""LLM judge (T2) — scores an artifact against a rubric, with citation
verification so a judge that hallucinates quality can't fool the score.
See docs/evals-observability-design.md §1.6.

Six mechanisms, all mechanical, all implemented here:
  M1 Fetch, never trust     — judge() re-fetches/re-reads the real artifact,
                               never the tool's self-reported summary text.
  M2 Deterministic gate     — caller's job (evals/runner.py's T1 must PASS
                               first); this module doesn't re-check T1.
  M3 Verified citations     — every criterion requires evidence: [exact
                               substrings]; any citation that doesn't
                               literally appear in the artifact forces that
                               criterion's score to 0 (UNVERIFIED_CITATION).
  M4 Blinding                — the judge prompt gets ONLY the artifact +
                               the ground-truth brief. No model id, no
                               prompt version, no git sha, no baseline.
  M5 n=2, gate on min        — judge_bundle() calls judge() twice, reports
                               mean, gates on min; large spread -> unstable.
  M6 Calibration             — evals/golden/labeled/ + calibrate() below.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

RUBRICS: dict[str, dict[str, int]] = {
    "prototype": {
        "account_specificity": 30,
        "demo_realism": 25,
        "brand_fit": 15,
        "value_prop_correctness": 20,
        "visual_polish": 10,  # advisory only — see judge_prototype()'s note
    },
    "walkthrough": {
        "narration_specificity": 40,
        "beats_match_page": 35,
        "pacing_and_length": 25,
    },
    "email": {
        "evidence_grounding": 35,
        "product_claim_accuracy": 30,
        "specificity_of_ask": 20,
        "voice_not_template": 15,
    },
    "deck": {
        "narrative_coherence": 60,
        "slide_copy_quality": 40,
    },
}

_JUDGE_MODEL = os.getenv("EVAL_JUDGE_MODEL", "gpt-5.6-sol")
_JUDGE_N = int(os.getenv("EVAL_JUDGE_N", "2"))
# Caught live: a real prototype runs 20-36KB and its interactive #demo
# section is typically the SECOND HALF of the file (hero/copy first,
# interactive block after) — the original 12,000-char cutoff sliced every
# real build off before the demo, and the judge (correctly, given what it
# could see) scored every one of them as having no working demo at all.
# 60K chars covers every artifact size observed this session with room
# to spare; gpt-5.6-sol's context window is nowhere near the constraint.
_MAX_ARTIFACT_CHARS = int(os.getenv("EVAL_JUDGE_MAX_ARTIFACT_CHARS", "60000"))


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


@dataclass
class CriterionScore:
    criterion: str
    weight: int
    raw_score: int          # 0-4 as the judge gave it
    verified: bool          # all its citations checked out
    evidence: list[str] = field(default_factory=list)
    fail_reasons: list[str] = field(default_factory=list)

    @property
    def score(self) -> int:
        """The score that actually counts — 0 if any citation failed
        verification, regardless of what the judge claimed."""
        return self.raw_score if self.verified else 0

    @property
    def weighted(self) -> float:
        return (self.score / 4.0) * self.weight


@dataclass
class JudgeResult:
    artifact_kind: str
    criteria: list[CriterionScore]
    unstable: bool = False

    @property
    def composite(self) -> float:
        return round(sum(c.weighted for c in self.criteria), 1)

    def __str__(self) -> str:
        lines = [f"composite: {self.composite}/100" + (" (UNSTABLE)" if self.unstable else "")]
        for c in self.criteria:
            flag = "" if c.verified else " [UNVERIFIED_CITATION -> forced 0]"
            lines.append(f"  {c.criterion} ({c.weight}pt): {c.score}/4{flag}")
            if c.fail_reasons:
                lines.append(f"    reasons: {'; '.join(c.fail_reasons)}")
        return "\n".join(lines)


def _verify_citations(evidence: list[str], artifact_text: str) -> bool:
    """M3 — every cited string must literally appear in the real artifact
    text (whitespace-collapsed, case-folded). Empty evidence list on a
    non-zero score also fails verification — a real score needs real proof."""
    if not evidence:
        return False
    norm_artifact = _normalize(artifact_text)
    return all(_normalize(e) in norm_artifact for e in evidence if e)


def _judge_prompt(artifact_kind: str, artifact_text: str, brief: dict[str, str]) -> str:
    criteria = RUBRICS[artifact_kind]
    criteria_desc = "\n".join(f"- {name} (weight {w}/100)" for name, w in criteria.items())
    return f"""You are a strict, skeptical reviewer scoring a {artifact_kind} an AI \
built for a specific sales prospect. Score 0-4 per criterion below. \
0 = "would work for any competitor by swapping the name" — that is the bar.

CRITERIA:
{criteria_desc}

GROUND TRUTH BRIEF (what this was supposed to be built for):
{json.dumps(brief, indent=2)}

THE ARTIFACT (the actual thing to judge — everything after this line):
---
{artifact_text[:_MAX_ARTIFACT_CHARS]}
---

For EACH criterion, respond with an object containing:
  "criterion": the exact criterion name
  "score": integer 0-4
  "evidence": array of 1-4 EXACT substrings copied verbatim from the artifact
    above that justify the score. Copy-paste exactly — do not paraphrase or
    summarize. A criterion with no real evidence in the artifact gets score 0
    and an empty evidence array — do not invent evidence to justify a score.
  "fail_reasons": array of short strings explaining what's missing/wrong
    (empty if score is 4)

Respond with a JSON object: {{"scores": [<one object per criterion above>]}}"""


def judge_once(artifact_kind: str, artifact_text: str, brief: dict[str, str]) -> JudgeResult:
    """One judge call (M1: artifact_text must be the REAL fetched/read
    content, never a tool's summary; M4: brief should contain only
    ground-truth facts — merchant/pain/startup — never model/prompt_version/
    git_sha/baseline)."""
    from ghost.llm import complete_json

    raw = complete_json(
        _judge_prompt(artifact_kind, artifact_text, brief),
        agent=f"eval_judge.{artifact_kind}",
        model=_JUDGE_MODEL,
        offline={"scores": []},
    )
    by_name = {s.get("criterion"): s for s in (raw.get("scores") or [])}

    criteria: list[CriterionScore] = []
    for name, weight in RUBRICS[artifact_kind].items():
        s = by_name.get(name, {})
        evidence = [str(e) for e in (s.get("evidence") or [])]
        raw_score = int(s.get("score", 0)) if isinstance(s.get("score"), (int, float)) else 0
        raw_score = max(0, min(4, raw_score))
        verified = raw_score == 0 or _verify_citations(evidence, artifact_text)
        criteria.append(CriterionScore(
            criterion=name, weight=weight, raw_score=raw_score, verified=verified,
            evidence=evidence, fail_reasons=[str(r) for r in (s.get("fail_reasons") or [])],
        ))
    return JudgeResult(artifact_kind=artifact_kind, criteria=criteria)


def calibrate() -> tuple[bool, str]:
    """M6 — run the labeled calibration set (evals/golden/labeled/) and
    confirm the judge still separates good from generic. Returns
    (all_passed, report_text). Does NOT include the dead-url case — that's
    a T1 (deterministic-gate) calibration check, not a judge one; see
    evals/golden/labeled/manifest.json's note on it."""
    import json as _json
    from pathlib import Path as _Path

    base = _Path(__file__).resolve().parent / "golden" / "labeled"
    manifest = _json.loads((base / "manifest.json").read_text())

    lines: list[str] = []
    all_ok = True
    for case_id, spec in manifest.items():
        if "artifact_path" not in spec:
            continue  # e.g. dead-url — a T1 case, not judged here
        text = (base / spec["artifact_path"]).read_text(encoding="utf-8")
        result = judge_bundle(spec["artifact_kind"], text, spec["brief"])
        lo, hi = spec["expected_composite_band"]
        ok = lo <= result.composite <= hi
        all_ok = all_ok and ok
        lines.append(f"{'PASS' if ok else 'FAIL'} {case_id}: composite={result.composite} "
                     f"(expected {lo}-{hi})")
        if not ok:
            lines.append(f"  {result}".replace("\n", "\n  "))
    return all_ok, "\n".join(lines)


def judge_bundle(artifact_kind: str, artifact_text: str, brief: dict[str, str]) -> JudgeResult:
    """M5 — n judge_once() calls, mean per-criterion score reported, but
    gated on the MIN across runs (conservative — a criterion that only
    sometimes verifies is not trustworthy). Flags `unstable` when any
    criterion's raw spread across runs exceeds 1.5 points."""
    if _JUDGE_N <= 1:
        return judge_once(artifact_kind, artifact_text, brief)

    runs = [judge_once(artifact_kind, artifact_text, brief) for _ in range(_JUDGE_N)]
    by_criterion: dict[str, list[CriterionScore]] = {}
    for run in runs:
        for c in run.criteria:
            by_criterion.setdefault(c.criterion, []).append(c)

    merged: list[CriterionScore] = []
    unstable = False
    for name, weight in RUBRICS[artifact_kind].items():
        scores = by_criterion.get(name, [])
        if not scores:
            merged.append(CriterionScore(criterion=name, weight=weight, raw_score=0,
                                         verified=False, fail_reasons=["no judge response"]))
            continue
        actual = [c.score for c in scores]  # post-verification (0 if unverified)
        if max(actual) - min(actual) > 1.5:
            unstable = True
        gated = min(actual)  # M5: gate on min, not mean — conservative
        all_reasons = [r for c in scores for r in c.fail_reasons]
        all_evidence = [e for c in scores for e in c.evidence]
        merged.append(CriterionScore(
            criterion=name, weight=weight, raw_score=gated, verified=True,  # already post-verified
            evidence=all_evidence, fail_reasons=list(dict.fromkeys(all_reasons))[:5],
        ))
    return JudgeResult(artifact_kind=artifact_kind, criteria=merged, unstable=unstable)
