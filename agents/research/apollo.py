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


def search_companies(keywords: list[str], *,
                     size_min: int = 5, size_max: int = 500,
                     limit: int = 25, per_page: int = 100) -> list[dict[str, Any]]:
    """Fetch candidate companies matching ``keywords`` (industry/segment tags),
    then filter client-side to startup-sized (Apollo starter plan ignores the
    ``organization_num_employees_ranges`` filter on server, so we do it here).

    Returns light company dicts — no credits spent (org search is free).
    """
    body = {
        "q_organization_keyword_tags": [k for k in keywords if k],
        "per_page": per_page,
        "page": 1,
    }
    try:
        resp = httpx.post(f"{_BASE}/organizations/search",
                          headers=_headers(), json=body, timeout=25)
    except httpx.HTTPError as exc:
        raise ApolloError(f"apollo network error: {exc}") from exc

    if resp.status_code == 401:
        raise ApolloError("Apollo rejected the API key (401).")
    if resp.status_code == 429:
        raise ApolloError("Apollo rate limit hit (429).")
    if resp.status_code != 200:
        raise ApolloError(f"apollo org search {resp.status_code}: {resp.text[:200]}")

    orgs = (resp.json() or {}).get("organizations", []) or []
    out: list[dict[str, Any]] = []
    for o in orgs:
        emp = o.get("estimated_num_employees") or 0
        if not (size_min <= emp <= size_max):
            continue
        domain = (o.get("primary_domain") or o.get("website_url") or "").strip()
        # strip protocol / trailing slash
        for prefix in ("https://", "http://"):
            if domain.startswith(prefix):
                domain = domain[len(prefix):]
        domain = domain.split("/", 1)[0].rstrip(".")
        if not domain or " " in domain:
            continue
        out.append({
            "name": o.get("name", ""),
            "domain": domain,
            "employees": emp,
            "industry": o.get("industry", ""),
            "founded_year": o.get("founded_year"),
            "linkedin_url": o.get("linkedin_url", ""),
            "short_description": (o.get("short_description") or "")[:400],
        })
        if len(out) >= limit:
            break
    return out


def enrich_organization(domain: str) -> dict[str, Any] | None:
    """Look up a company in Apollo's org index by domain. Free, no credits.
    Returns None on 404 (Apollo doesn't know the company) or on any error."""
    d = (domain or "").strip().lower()
    for p in ("https://", "http://", "www."):
        if d.startswith(p):
            d = d[len(p):]
    d = d.split("/", 1)[0].rstrip(".")
    if not d or "." not in d:
        return None
    try:
        resp = httpx.get(f"{_BASE}/organizations/enrich",
                        headers=_headers(), params={"domain": d}, timeout=15)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    org = (resp.json() or {}).get("organization", {}) or {}
    return org or None


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
        # `mixed_people/api_search` is the current API-caller endpoint;
        # the old `mixed_people/search` is deprecated for API keys.
        resp = httpx.post(f"{_BASE}/mixed_people/api_search",
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
            # the api_search endpoint often omits the name; the reveal has it
            if not contact["name"] and revealed.get("name"):
                contact["name"] = revealed["name"]
            if not contact["title"] and revealed.get("title"):
                contact["title"] = revealed["title"]
        except ApolloError as exc:
            contact["email_note"] = str(exc)
    return contact


def _title_rank(title: str) -> int:
    t = title.lower()
    for i, pref in enumerate(DEFAULT_TITLES):
        if pref.lower() in t:
            return i
    return len(DEFAULT_TITLES)
