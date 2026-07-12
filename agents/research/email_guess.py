"""Derive candidate emails from (first, last, domain).

Not a verifier — just a ranked list of the patterns most companies use. The
Sales agent later can plug in a real verifier (Neverbounce / Hunter's
email-verifier / SMTP RCPT probe). Ordering is by rough industry frequency,
per Hunter's public pattern stats.
"""

from __future__ import annotations

import re


_PATTERNS: list[tuple[str, str]] = [
    ("first.last",   "{first}.{last}@{domain}"),
    ("first",        "{first}@{domain}"),
    ("firstlast",    "{first}{last}@{domain}"),
    ("flast",        "{f}{last}@{domain}"),
    ("first_last",   "{first}_{last}@{domain}"),
    ("firstl",       "{first}{l}@{domain}"),
    ("last",         "{last}@{domain}"),
    ("first-last",   "{first}-{last}@{domain}"),
]


_SLUG_RX = re.compile(r"[^a-z0-9]+")


def _slug(s: str) -> str:
    return _SLUG_RX.sub("", s.strip().lower())


def _clean_domain(domain: str) -> str:
    d = domain.strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = d.split("/", 1)[0]
    d = re.sub(r"^www\.", "", d)
    return d


def guess(first: str, last: str, domain: str, *, top: int = 5) -> list[dict[str, str]]:
    """Return the top ``top`` candidate `{pattern, email}` guesses."""
    f = _slug(first)
    l = _slug(last)
    d = _clean_domain(domain)
    if not f or not d:
        return []
    fi = f[:1]
    li = l[:1] if l else ""
    seen = set()
    out: list[dict[str, str]] = []
    for name, tpl in _PATTERNS:
        email = tpl.format(first=f, last=l, f=fi, l=li, domain=d)
        # patterns that need a last name but we don't have one → skip
        if not l and any(k in tpl for k in ("last", "l}")):
            continue
        if email in seen or "@" not in email or email.startswith("@") or email.endswith("@"):
            continue
        seen.add(email)
        out.append({"pattern": name, "email": email})
        if len(out) >= top:
            break
    return out
