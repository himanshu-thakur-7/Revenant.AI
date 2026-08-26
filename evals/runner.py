"""Ties the deterministic (T1) checks together for one Bundle, and
(run_t1_and_t2 / score_bundle) gates the LLM judge (T2) behind them per
docs/evals-observability-design.md §1.6 M2 — the judge never runs on an
artifact that already failed its deterministic checks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from evals.bundle import Bundle
from evals.checks import Check
from evals.checks import deck_, email_, html_, video_


def prototype_checks(b: Bundle) -> list[Check]:
    from evals.checks.http_ import body_size_at_least, content_type_is, not_ngrok_interstitial, url_alive

    url, path = b.prototype_url, b.prototype_html_path
    return [
        url_alive(url),
        content_type_is(url, "text/html"),
        not_ngrok_interstitial(url),
        body_size_at_least(url, 8_000),
        html_.element_id_contract(url, path),
        html_.no_external_img(url, path),
        html_.demo_input_prefilled(url, path),
        html_.specificity_lint(url, path, merchant=b.merchant, merchant_domain=b.merchant_domain,
                               pain=b.pain, contact_name=b.contact_name, contact_title=b.contact_title),
        html_.renders_clean(url),
    ]


def walkthrough_checks(b: Bundle) -> list[Check]:
    url, path = b.walkthrough_url, b.walkthrough_mp4_path
    return [
        video_.video_url_alive(url),
        video_.content_type_is_video(url),
        video_.mp4_size_at_least(url, path),
        video_.has_video_and_audio_streams(url, path),
        video_.duration_between(url, path),
        video_.audio_not_silent(url, path),
    ]


def deck_checks(b: Bundle) -> list[Check]:
    path = b.deck_pptx_path
    return [
        deck_.pptx_opens(path),
        deck_.slide_count_between(path),
        deck_.slide_arc(path, b.startup, b.merchant),
        deck_.copy_limits(path),
        deck_.no_placeholder_text(path),
    ]


def email_checks(b: Bundle) -> list[Check]:
    path = b.email_md_path
    return [
        email_.md_exists_nonempty(path),
        email_.subject_len_ok(b.email_subject),
        email_.no_banned_openers(path),
        email_.evidence_grounding(path, merchant=b.merchant, merchant_domain=b.merchant_domain,
                                  pain=b.pain, contact_name=b.contact_name,
                                  contact_title=b.contact_title),
        email_.no_placeholder_name(path),
        email_.links_present_and_alive(path, b.prototype_url, b.walkthrough_url),
    ]


_KIND_TO_CHECKS = {
    "prototype": prototype_checks,
    "walkthrough": walkthrough_checks,
    "deck": deck_checks,
    "email": email_checks,
}


def run_t1(b: Bundle) -> dict[str, list[Check]]:
    """Run every applicable check group — only for artifact kinds the
    bundle actually claims to have (per Bundle.artifacts()); a campaign
    that only built a prototype (no film/draft yet) isn't penalized for
    a walkthrough that was never supposed to exist yet."""
    claimed = b.artifacts()
    return {
        kind: fn(b)
        for kind, fn in _KIND_TO_CHECKS.items()
        if claimed.get(kind)
    }


def artifact_pass(checks: list[Check]) -> bool:
    return all(c.passed for c in checks)


def summarize(results: dict[str, list[Check]]) -> str:
    lines = []
    for kind, checks in results.items():
        ok = artifact_pass(checks)
        lines.append(f"== {kind}: {'PASS' if ok else 'FAIL'} ({sum(c.passed for c in checks)}/{len(checks)}) ==")
        for c in checks:
            lines.append(f"  {c}")
    return "\n".join(lines)


# ── T2 (LLM judge) — gated behind T1, per §1.6 M2 ──────────────────────
# Judged kinds are intentionally limited to text-native artifacts for now
# (prototype HTML, email markdown) — a deck needs python-pptx text
# extraction and a walkthrough needs its narration transcript to judge
# meaningfully; both are real, doable extensions, not done here. Scoping
# this explicitly rather than silently skipping: see the "not yet judged"
# note in score_bundle()'s return.
_JUDGEABLE_KINDS = {"prototype", "email"}


def _artifact_text_for_judge(b: Bundle, kind: str) -> str:
    if kind == "prototype":
        path = b.prototype_html_path
    elif kind == "email":
        path = b.email_md_path
    else:
        return ""
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8", errors="replace")
    return ""


def score_bundle(b: Bundle) -> dict[str, dict[str, Any]]:
    """T1 for every claimed artifact kind; T2 (LLM judge) additionally for
    judgeable kinds that PASSED T1. Returns
    {kind: {"checks": [Check...], "t1_pass": bool, "judge": JudgeResult|None}}.
    """
    from evals.judge import judge_bundle

    t1 = run_t1(b)
    out: dict[str, dict[str, Any]] = {}
    for kind, checks in t1.items():
        t1_pass = artifact_pass(checks)
        judge_result = None
        if t1_pass and kind in _JUDGEABLE_KINDS:
            text = _artifact_text_for_judge(b, kind)
            if text:
                brief = {"startup": b.startup, "merchant": b.merchant, "pain": b.pain}
                judge_result = judge_bundle(kind, text, brief)
        out[kind] = {"checks": checks, "t1_pass": t1_pass, "judge": judge_result}
    return out


def score_summary(scored: dict[str, dict[str, Any]]) -> str:
    lines = []
    for kind, info in scored.items():
        checks = info["checks"]
        t1_ok = info["t1_pass"]
        lines.append(f"== {kind}: T1 {'PASS' if t1_ok else 'FAIL'} "
                     f"({sum(c.passed for c in checks)}/{len(checks)}) ==")
        for c in checks:
            lines.append(f"  {c}")
        judge = info.get("judge")
        if judge is not None:
            lines.append(f"  T2 judge: {str(judge).replace(chr(10), chr(10) + '  ')}")
        elif t1_ok and kind in _JUDGEABLE_KINDS:
            lines.append("  T2 judge: skipped (no artifact text available)")
    return "\n".join(lines)


def bundle_pass(scored: dict[str, dict[str, Any]], *, judge_threshold: float = 70.0) -> bool:
    """§1.7's contract: artifact_pass := all T1 PASS and (composite >= 70
    OR not judged). A missing/optional artifact (kind absent from `scored`
    entirely, e.g. no walkthrough built yet) does not fail the bundle —
    only kinds actually present are checked."""
    for info in scored.values():
        if not info["t1_pass"]:
            return False
        judge = info.get("judge")
        if judge is not None and judge.composite < judge_threshold:
            return False
    return True
