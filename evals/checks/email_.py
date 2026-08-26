"""Email-draft checks — read the actual .md file on disk, same discipline
as everywhere else: never trust the tool's own summary of what it wrote.
"""

from __future__ import annotations

import re
from pathlib import Path

from evals.checks import Check

_BANNED_OPENERS = (
    "quick question", "reaching out", "circling back", "hope this finds you",
    "touching base", "i wanted to",
)
_PLACEHOLDER_MARKERS = ("[name]", "{{", "<recipient>", "<company>", "[company]")


def _read(email_md_path: str) -> str | None:
    if not email_md_path or not Path(email_md_path).exists():
        return None
    return Path(email_md_path).read_text(encoding="utf-8", errors="replace")


def md_exists_nonempty(email_md_path: str, min_chars: int = 400,
                       *, name: str = "md_exists_nonempty") -> Check:
    body = _read(email_md_path)
    if body is None:
        return Check(name, False, f"no file at {email_md_path or '(empty path)'}")
    n = len(body)
    return Check(name, n >= min_chars, f"{n} chars (want >= {min_chars})", measured=n)


def subject_len_ok(email_subject: str, max_chars: int = 60,
                   *, name: str = "subject_len") -> Check:
    n = len(email_subject or "")
    ok = 0 < n <= max_chars
    return Check(name, ok, f"{n} chars (want 1-{max_chars})", measured=n)


def no_banned_openers(email_md_path: str, *, name: str = "no_banned_openers") -> Check:
    body = _read(email_md_path)
    if body is None:
        return Check(name, False, "no file to check")
    lower = body.lower()
    hits = [p for p in _BANNED_OPENERS if p in lower]
    return Check(name, not hits, f"found: {hits}" if hits else "clean", measured=hits)


def evidence_grounding(email_md_path: str, merchant: str = "", merchant_domain: str = "",
                       pain: str = "", contact_name: str = "", contact_title: str = "",
                       min_clues: int = 2, *, name: str = "evidence_grounding") -> Check:
    """Reuses agents.engineer.tools._prospect_clues — the same clue
    extraction the specificity linter uses for the prototype, applied
    here to the email body."""
    body = _read(email_md_path)
    if body is None:
        return Check(name, False, "no file to check")
    from agents.engineer.tools import _prospect_clues

    prospect = {
        "company_name": merchant, "company_domain": merchant_domain,
        "fit_rationale": pain, "contact": {"name": contact_name, "title": contact_title},
    }
    clues = _prospect_clues(prospect)
    lower = body.lower()
    hits = [c for c in clues if c in lower]
    n = len(set(hits))
    return Check(name, n >= min_clues, f"{n}/{len(clues)} clues present (want >= {min_clues})",
                 measured=n)


def no_placeholder_name(email_md_path: str, *, name: str = "no_placeholder_name") -> Check:
    body = _read(email_md_path)
    if body is None:
        return Check(name, False, "no file to check")
    lower = body.lower()
    hits = [m for m in _PLACEHOLDER_MARKERS if m in lower]
    return Check(name, not hits, f"found: {hits}" if hits else "clean", measured=hits)


def links_present_and_alive(email_md_path: str, prototype_url: str = "", walkthrough_url: str = "",
                             *, name: str = "links_present_and_alive") -> Check:
    body = _read(email_md_path)
    if body is None:
        return Check(name, False, "no file to check")
    from evals.checks.http_ import url_alive

    problems = []
    for label, url in (("prototype", prototype_url), ("walkthrough", walkthrough_url)):
        if not url:
            continue  # optional artifact — absence isn't this check's job
        if url not in body:
            problems.append(f"{label} URL not present in email body")
            continue
        c = url_alive(url, name=f"{label}_alive")
        if not c.passed:
            problems.append(f"{label} URL present but not alive: {c.detail}")
    return Check(name, not problems, "; ".join(problems) if problems else "present and alive")
