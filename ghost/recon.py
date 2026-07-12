"""Reconnaissance — turn the raw internet into scored, cited leads.

In ``live`` mode this drives **Linkup** to search for companies leaking the
seller's target pain, then fetches cheap forensic sources (careers page,
GitHub org, status page, eng blog). In ``offline`` mode it replays
:mod:`ghost.fixtures`. Either way the output is a list of :class:`Lead` with
verbatim :class:`Evidence` attached — the gate scores them next.
"""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import quote_plus

import httpx

from .config import settings
from .events import DETECTIVE, LEDGER, mission
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
    mission.emit(
        2, DETECTIVE,
        f"03:00 — the office is dark. Waking up with one directive: find companies "
        f"leaking the pain {seller.name} solves.",
        kind="info", dwell=0.5,
    )
    mission.emit(
        2, DETECTIVE,
        f"Profiling the seller: {seller.one_liner} ICP → {seller.icp}",
        kind="info",
    )
    for kw in _public_queries(seller):
        mission.emit(
            2, DETECTIVE,
            f'Formulating forensic query → "{kw}" ∩ hiring signals ∩ status pages ∩ careers boards',
            kind="query", dwell=1.1,
        )

    if settings.require_live("linkup_api_key"):
        raw_leads = _live_hunt(seller, limit)
        mission.emit(2, DETECTIVE, "Linkup sweep complete — live web results in.", kind="info")
    elif not settings.offline:
        raw_leads = _public_hunt(seller, limit)
        mission.emit(
            2,
            DETECTIVE,
            "Public-source sweep complete — no canned fixtures, no private data, no API keys.",
            kind="info",
        )
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
        forensics = _forensic_scores(raw)
        mission.emit(
            2, DETECTIVE,
            f"Candidate surfaced: {lead.company_name} ({lead.company_domain}) — "
            f"{lead.person_name or 'decision-maker unknown'}, {lead.person_title or 'title tbd'}",
            company=lead.company_name, kind="info", dwell=1.8,
        )
        for ev in forensics.evidence[:2]:
            mission.emit(
                2, LEDGER,
                f'Grounding verbatim evidence [{ev.source}] — "{ev.excerpt}"',
                company=lead.company_name, kind="evidence", dwell=1.9,
                payload={"source": ev.source, "url": ev.url},
            )
        out.append((lead, forensics))
    log.ok(f"Recon surfaced {len(out)} candidate leads")
    mission.emit(
        2, DETECTIVE,
        f"Sweep complete — {len(out)} candidates on the board. No guesses; every claim "
        f"is written to the truth ledger with a source.",
        kind="info",
    )
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


def _public_hunt(seller: SellerProfile, limit: int) -> list[dict[str, Any]]:  # pragma: no cover
    """No-key live reconnaissance over public sources.

    This is the buildathon-safe path that makes Act II real even before Linkup
    credentials are present. It searches public job posts (Remotive) and public
    GitHub issues, extracts verbatim excerpts, groups signals by company/repo,
    and falls back to fixtures only if the public web is unavailable.
    """
    mission.emit(
        2,
        DETECTIVE,
        "No Linkup key detected. Switching to credential-free live reconnaissance: "
        "public job posts plus public GitHub issue forensics.",
        kind="query",
        dwell=1.2,
    )
    leads: dict[str, dict[str, Any]] = {}
    target_pool = max(limit * 4, 8)

    for kw in _public_queries(seller):
        mission.emit(
            2,
            DETECTIVE,
            f'Live query → Remotive jobs for "{kw}"',
            kind="query",
            dwell=1.0,
        )
        for raw in _remotive_jobs(kw)[:8]:
            lead = _lead_from_remotive(raw, kw)
            if lead:
                _merge_public_lead(leads, lead)
            if len(leads) >= target_pool:
                break
        if len(leads) >= target_pool:
            break

    for kw in _public_queries(seller):
        mission.emit(
            2,
            DETECTIVE,
            f'Live query → GitHub issues for "{kw}"',
            kind="query",
            dwell=1.0,
        )
        for raw in _github_issues(kw)[:8]:
            lead = _lead_from_github(raw, kw)
            if lead:
                _merge_public_lead(leads, lead)
            if len(leads) >= target_pool:
                break
        if len(leads) >= target_pool:
            break

    rows = _gate_public_rows(sorted(leads.values(), key=_public_rank, reverse=True), limit)
    if rows:
        log.ok(f"[recon] public live sweep surfaced {len(rows)} lead(s)")
        return rows
    log.warn("[recon] public live sweep returned nothing; using fixtures")
    return CANNED_LEADS.get(seller.slug, [])[:limit]


def _remotive_jobs(keyword: str) -> list[dict[str, Any]]:  # pragma: no cover
    try:
        resp = httpx.get(
            f"https://remotive.com/api/remote-jobs?search={quote_plus(keyword)}",
            headers={"User-Agent": "RevenantAI-demo/0.1"},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("jobs", [])
    except Exception as exc:
        log.warn(f"[recon] Remotive query failed for {keyword!r}: {exc!r}")
        return []


def _public_queries(seller: SellerProfile) -> list[str]:
    seen: list[str] = []
    for q in [
        *seller.pain_keywords[:4],
        "customer operations",
        "customer support",
        "support operations",
        "customer success operations",
    ]:
        if q not in seen:
            seen.append(q)
    return seen


def _github_issues(keyword: str) -> list[dict[str, Any]]:  # pragma: no cover
    query = quote_plus(f'"{keyword}" is:issue is:open')
    try:
        resp = httpx.get(
            f"https://api.github.com/search/issues?q={query}&sort=updated&order=desc&per_page=10",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "RevenantAI-demo/0.1",
            },
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("items", [])
    except Exception as exc:
        log.warn(f"[recon] GitHub query failed for {keyword!r}: {exc!r}")
        return []


def _lead_from_remotive(raw: dict[str, Any], keyword: str) -> dict[str, Any] | None:
    company = (raw.get("company_name") or "").strip()
    title = (raw.get("title") or "").strip()
    desc = _clean_html(raw.get("description", ""))
    url = raw.get("url") or ""
    if not company or not desc:
        return None
    signal_text = f"{title} {desc}"
    if not _has_support_ops_signal(title, desc):
        return None

    excerpt = _excerpt(desc, keyword) or desc[:240]
    title_role = _contains_any(title, ROLE_SUPPORT_TERMS)
    domain = _slug_domain(company, "jobs.public")
    return {
        "company_name": company[:80],
        "company_domain": domain,
        "person_name": "",
        "person_title": "Support Operations / Hiring Team",
        "job_description": f"{title}. {desc[:1200]}",
        "forensics": {
            "careers_score": 0.88 if _contains_any(signal_text, STRONG_SUPPORT_TERMS) or title_role else 0.68,
            "github_score": 0.0,
            "status_score": 0.0,
            "eng_blog_score": 0.15,
        },
        "evidence": [
            {
                "source": "careers",
                "url": url,
                "excerpt": excerpt,
                "weight": 0.32,
            }
        ],
    }


def _lead_from_github(raw: dict[str, Any], keyword: str) -> dict[str, Any] | None:
    repo = raw.get("repository_url", "").rsplit("/", 2)[-2:]
    if len(repo) != 2:
        return None
    owner, name = repo
    body = raw.get("body") or raw.get("title") or ""
    title = raw.get("title") or ""
    url = raw.get("html_url") or ""
    excerpt = _excerpt(f"{title}. {body}", keyword) or title[:240]
    if not _has_support_ops_signal(title, body):
        return None
    company = owner.replace("-", " ").replace("_", " ").title()
    return {
        "company_name": company[:80],
        "company_domain": f"github.com/{owner}/{name}",
        "person_name": "",
        "person_title": "Engineering / Support Maintainers",
        "job_description": f"{title}. {body[:1200]}",
        "forensics": {
            "careers_score": 0.0,
            "github_score": 0.86 if _contains_any(f"{title} {body}", STRONG_SUPPORT_TERMS) else 0.68,
            "status_score": 0.15,
            "eng_blog_score": 0.0,
        },
        "evidence": [
            {
                "source": "github",
                "url": url,
                "excerpt": excerpt,
                "weight": 0.34,
            }
        ],
    }


STRONG_SUPPORT_TERMS = (
    "support backlog",
    "ticket triage",
    "support ticket",
    "support tickets",
    "support queue",
    "helpdesk",
    "backlog",
    "ticket routing",
    "sla",
    "sla breach",
    "sla risk",
    "manual routing",
    "zendesk",
    "intercom",
    "freshdesk",
    "helpscout",
    "customer escalation",
    "escalation workflow",
    "first response",
    "first-response",
    "reply time",
    "routing rules",
)

ROLE_SUPPORT_TERMS = (
    "customer support",
    "customer operations",
    "support operations",
    "customer success",
    "client success",
    "support manager",
    "support lead",
)

SUPPORT_TERMS = STRONG_SUPPORT_TERMS + ROLE_SUPPORT_TERMS


def _merge_public_lead(leads: dict[str, dict[str, Any]], lead: dict[str, Any]) -> None:
    key = lead["company_domain"].lower()
    if key not in leads:
        leads[key] = lead
        return
    existing = leads[key]
    existing["job_description"] = (existing["job_description"] + "\n\n" + lead["job_description"])[:1800]
    existing["evidence"].extend(lead.get("evidence", []))
    for field in ("careers_score", "github_score", "status_score", "eng_blog_score"):
        existing["forensics"][field] = max(
            float(existing["forensics"].get(field, 0.0)),
            float(lead["forensics"].get(field, 0.0)),
        )


def _public_rank(row: dict[str, Any]) -> float:
    f = row.get("forensics", {})
    text = row.get("job_description", "").lower()
    title = text.split(".", 1)[0]
    role_bonus = 0.0
    if "customer operations" in title or "support operations" in title:
        role_bonus = 0.35
    elif "customer support" in title or "support manager" in title or "support lead" in title:
        role_bonus = 0.25
    elif "client success" in title or "customer success" in title:
        role_bonus = 0.08
    strong_bonus = 0.25 if _contains_any(text, STRONG_SUPPORT_TERMS) else 0.0
    return (
        float(f.get("careers_score", 0.0))
        + float(f.get("github_score", 0.0))
        + 0.2 * len(row.get("evidence", []))
        + role_bonus
        + strong_bonus
    )


def _gate_public_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Keep public recon honest by routing candidates through the same gate."""
    from .gate import evaluate
    from .models import Tier

    kept: list[dict[str, Any]] = []
    for row in rows:
        score = evaluate(row.get("job_description", ""), _forensic_scores(row))
        if score.tier != Tier.KILL:
            kept.append(row)
        if len(kept) >= limit:
            break
    return kept


def _clean_html(text: str) -> str:
    text = re.sub(r"<(script|style).*?</\1>", " ", text or "", flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _excerpt(text: str, keyword: str, width: int = 260) -> str:
    clean = _clean_html(text)
    m = re.search(re.escape(keyword), clean, flags=re.I)
    if not m:
        for term in STRONG_SUPPORT_TERMS + ROLE_SUPPORT_TERMS:
            m = re.search(re.escape(term), clean, flags=re.I)
            if m:
                break
    if not m:
        return clean[:width].strip()
    start = max(0, m.start() - width // 3)
    end = min(len(clean), m.end() + width)
    return clean[start:end].strip()


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    low = text.lower()
    for term in terms:
        if re.fullmatch(r"[a-z0-9]+", term):
            if re.search(rf"\b{re.escape(term)}\b", low):
                return True
        elif term in low:
            return True
    return False


def _has_support_ops_signal(title: str, body: str) -> bool:
    """Avoid promoting generic uses of "support"; require operational pain."""
    joined = f"{title} {body}"
    if _contains_any(joined, STRONG_SUPPORT_TERMS):
        return True
    role = _contains_any(title, ROLE_SUPPORT_TERMS)
    operational_context = _contains_any(
        joined,
        (
            "queue",
            "routing",
            "triage",
            "escalation",
            "operations",
            "operational",
            "response time",
            "first response",
            "customers",
            "customer",
            "tickets",
            "tight timeline",
            "satisfaction ratings",
            "world-class service",
            "crm",
            "intercom",
            "zendesk",
        ),
    )
    return role and operational_context


def _slug_domain(company: str, suffix: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-") or "prospect"
    return f"{slug}.{suffix}"


def _domain(url: str) -> str:
    try:
        return url.split("//", 1)[-1].split("/", 1)[0].replace("www.", "")
    except Exception:
        return ""
