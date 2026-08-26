"""Ties the deterministic (T1) checks together for one Bundle.

Deliberately does NOT include the LLM judge (T2) — that's evals/judge.py,
a separate module, because T1 is a hard gate: docs/evals-observability-
design.md §1.6 M2 says the judge must never run on an artifact that
failed its deterministic checks, so run_t1() has to be callable and
complete on its own before anything decides whether to call the judge.
"""

from __future__ import annotations

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
