"""Apollo.io client — decision-maker discovery + email reveal.

Two-step flow on the starter plan:

1. **People search** (`mixed_people/search`) — free on all plans. Finds
   people at a company domain filtered by title. Returns names, titles,
   LinkedIn URLs, and *obfuscated* emails.
2. **Person match** (`people/match`) — spends an email credit to reveal the
   verified work email for one person. We only call this for the single
   best-ranked contact, never in bulk.

Every function degrades to a clear error string when the key is missing or
quota is exhausted — the Research agent relays that to the founder instead
of silently failing.
"""

from __future__ import annotations

from typing import Any

import httpx

from ghost.config import settings


_BASE = "https://api.apollo.io/api/v1"

# Titles worth reaching for a dev-tool / API product, best first.
DEFAULT_TITLES = [
    "CEO", "CTO", "Co-Founder", "Founder",
    "VP of Engineering", "VP Engineering", "Head of Engineering",
    "Head of Security", "CISO", "Chief Compliance Officer",
    "Engineering Manager",
]


class ApolloError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    if not settings.apollo_api_key:
        raise ApolloError(
            "APOLLO_API_KEY not configured — add it to .env "
            "(app.apollo.io → Settings → Integrations → API)."
        )
    return {
        "X-Api-Key": settings.apollo_api_key,
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }


def search_people(company_domain: str, *, titles: list[str] | None = None,
                  limit: int = 5) -> list[dict[str, Any]]:
    """People search by company domain. Returns light person dicts,
    ranked by our title preference. Free — burns no credits."""
    body = {
        "q_organization_domains_list": [company_domain.lower().strip()],
        "person_titles": titles or DEFAULT_TITLES,
        "page": 1,
        "per_page": max(limit, 5),
    }
    try:
        resp = httpx.post(f"{_BASE}/mixed_people/search",
                          headers=_headers(), json=body, timeout=20)
    except httpx.HTTPError as exc:
        raise ApolloError(f"apollo network error: {exc}") from exc

    if resp.status_code == 401:
        raise ApolloError("Apollo rejected the API key (401). Regenerate it "
                          "at app.apollo.io → Settings → Integrations → API.")
    if resp.status_code == 429:
        raise ApolloError("Apollo rate limit hit (429) — wait a minute and retry.")
    if resp.status_code != 200:
        raise ApolloError(f"apollo {resp.status_code}: {resp.text[:200]}")

    people = (resp.json() or {}).get("people", []) or []
    ranked = sorted(people, key=lambda p: _title_rank(p.get("title") or ""))
    out = []
    for p in ranked[:limit]:
        out.append({
            "apollo_id": p.get("id", ""),
            "name": p.get("name", ""),
            "title": p.get("title", ""),
            "linkedin_url": p.get("linkedin_url", ""),
            "email_status": p.get("email_status", ""),
            # search returns obfuscated emails; reveal is a separate call
            "email_obfuscated": p.get("email", ""),
        })
    return out


def reveal_email(apollo_id: str) -> dict[str, Any]:
    """Spend ONE email credit to reveal a verified work email."""
    body = {"id": apollo_id, "reveal_personal_emails": False}
    try:
        resp = httpx.post(f"{_BASE}/people/match",
                          headers=_headers(), json=body, timeout=20)
    except httpx.HTTPError as exc:
        raise ApolloError(f"apollo network error: {exc}") from exc

    if resp.status_code == 402 or "insufficient" in resp.text.lower():
        raise ApolloError("Apollo email credits exhausted — the plan's reveal "
                          "quota is used up for this cycle.")
    if resp.status_code != 200:
        raise ApolloError(f"apollo match {resp.status_code}: {resp.text[:200]}")

    person = (resp.json() or {}).get("person", {}) or {}
    email = person.get("email", "")
    if not email or email.endswith("@domain.com"):
        return {"email": "", "note": "Apollo has no verified email for this person."}
    return {
        "email": email,
        "email_status": person.get("email_status", ""),
        "name": person.get("name", ""),
        "title": person.get("title", ""),
    }


def find_best_contact(company_domain: str, *,
                      titles: list[str] | None = None) -> dict[str, Any]:
    """One-shot: search → pick best-titled person → reveal their email.
    Spends at most one credit. Returns a contact dict or raises ApolloError."""
    people = search_people(company_domain, titles=titles, limit=5)
    if not people:
        return {"error": f"Apollo found nobody at {company_domain} "
                          f"matching {titles or DEFAULT_TITLES[:4]}…"}
    best = people[0]
    contact: dict[str, Any] = {
        "name": best["name"],
        "title": best["title"],
        "linkedin_url": best["linkedin_url"],
        "email": "",
        "email_verified": False,
        "alternates": [
            {"name": p["name"], "title": p["title"]} for p in people[1:3]
        ],
    }
    if best["apollo_id"]:
        try:
            revealed = reveal_email(best["apollo_id"])
            if revealed.get("email"):
                contact["email"] = revealed["email"]
                contact["email_verified"] = revealed.get(
                    "email_status", "") == "verified"
        except ApolloError as exc:
            contact["email_note"] = str(exc)
    return contact


def _title_rank(title: str) -> int:
    t = title.lower()
    for i, pref in enumerate(DEFAULT_TITLES):
        if pref.lower() in t:
            return i
    return len(DEFAULT_TITLES)
