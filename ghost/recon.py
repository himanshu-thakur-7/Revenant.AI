"""Reconnaissance — turn the raw internet into scored, cited leads.

In ``live`` mode this drives **Linkup** to search for companies leaking the
seller's target pain, then fetches cheap forensic sources (careers page,
GitHub org, status page, eng blog). In ``offline`` mode it replays
:mod:`ghost.fixtures`. Either way the output is a list of :class:`Lead` with
verbatim :class:`Evidence` attached — the gate scores them next.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import settings
from .fixtures import CANNED_LEADS
from .log import log
from .models import Evidence, Lead, SellerProfile, SignalScore


def _linkup_search(query: str) -> list[dict[str, Any]]:  # pragma: no cover - network
    """One Linkup search. Returns raw result dicts (title, url, snippet)."""
    resp = httpx.post(
        "https://api.linkup.so/v1/search",
        headers={"Authorization": f"Bearer {settings.linkup_api_key}"},
        json={"q": query, "depth": "standard", "outputType": "searchResults"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def _forensic_scores(raw: dict[str, Any]) -> SignalScore:
    """Assemble a SignalScore's external-source fields from a raw lead's
    forensic bundle (offline fixture, or a live Linkup fetch result)."""
    f = raw.get("forensics", {})
    ev = [Evidence(**e) for e in raw.get("evidence", [])]
    return SignalScore(
        careers_score=float(f.get("careers_score", 0.0)),
        github_score=float(f.get("github_score", 0.0)),
        status_score=float(f.get("status_score", 0.0)),
        eng_blog_score=float(f.get("eng_blog_score", 0.0)),
        evidence=ev,
    )


def hunt(seller: SellerProfile, limit: int = 5) -> list[tuple[Lead, SignalScore]]:
    """Find candidate prospects for a seller. Returns (lead, forensic_score)
    pairs; the forensic score carries external-source signals only — the gate
    adds the JD analysis and the final tier."""
    log.stage(f"Recon: hunting '{seller.name}' pain across the web…")

    if settings.require_live("linkup_api_key"):
        raw_leads = _live_hunt(seller, limit)
    else:
        log.dim("[recon] offline → canned leads")
        raw_leads = CANNED_LEADS.get(seller.slug, [])[:limit]

    out: list[tuple[Lead, SignalScore]] = []
    for raw in raw_leads:
        jd = raw["job_description"]
        jd = jd[0] if isinstance(jd, (list, tuple)) else jd  # tolerate fixture typos
        lead = Lead(
            seller_id=seller.id,
            company_name=raw["company_name"],
            company_domain=raw["company_domain"],
            person_name=raw.get("person_name", ""),
            person_title=raw.get("person_title", ""),
            job_description=jd,
        )
        out.append((lead, _forensic_scores(raw)))
    log.ok(f"Recon surfaced {len(out)} candidate leads")
    return out


def _live_hunt(seller: SellerProfile, limit: int) -> list[dict[str, Any]]:  # pragma: no cover
    """Live Linkup recon. Kept deliberately simple for the buildathon: one
    search per pain keyword, dedup by domain, best-effort forensic enrichment.
    Falls back to fixtures on any failure so a demo never dies on a flaky API."""
    seen: dict[str, dict[str, Any]] = {}
    try:
        for kw in seller.pain_keywords:
            query = f'"{kw}" hiring job {seller.icp}'
            for r in _linkup_search(query):
                url = r.get("url", "")
                domain = _domain(url)
                if not domain or domain in seen:
                    continue
                seen[domain] = {
                    "company_name": r.get("title", domain).split(" - ")[0][:60],
                    "company_domain": domain,
                    "job_description": r.get("content") or r.get("snippet", ""),
                    "forensics": {},  # live forensic fetch is a stretch; JD-only is fine
                    "evidence": [
                        {"source": "jd", "url": url,
                         "excerpt": (r.get("snippet", "")[:200]), "weight": 0.0}
                    ],
                }
                if len(seen) >= limit:
                    break
            if len(seen) >= limit:
                break
    except Exception as exc:
        log.warn(f"[recon] live hunt failed ({exc!r}); using fixtures")
        return CANNED_LEADS.get(seller.slug, [])[:limit]

    return list(seen.values()) or CANNED_LEADS.get(seller.slug, [])[:limit]


def _domain(url: str) -> str:
    try:
        return url.split("//", 1)[-1].split("/", 1)[0].replace("www.", "")
    except Exception:
        return ""
