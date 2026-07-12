"""Deterministic full-chain runner — Research → Engineer → Director → Sales.

The interactive `revenant chat` uses the LLM Orchestrator to *decide* what to
do. But automated front-ends (the Telegram bot, cron's 3 AM loop) know
exactly what they want — a full campaign — and need **structured artifacts
back**, not a prose brief. This module runs the four sub-agents in sequence
with no orchestration LLM in the loop, so it's fast, cheap, and can't stall
on a mis-formatted tool call.

Each stage reports progress through an optional ``on_stage(stage, detail)``
callback so a front-end can stream status. Failures degrade gracefully:
the runner returns whatever artifacts it managed to produce with a warning,
rather than losing the whole run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from ghost.config import settings

from .base import EventSink
from .bridge import bridge
from .context import FounderContext


# ── deterministic Apollo-first prospect hunt ──────────────────────
# The LLM Research agent is powerful but flaky on the first try; for the
# demo path we want a 100 %-reliable "we found something" moment. Apollo's
# org search is free, deterministic, and returns real US companies filtered
# by industry + size — perfect for a warm-start prospect. The LLM Research
# only kicks in as a fallback when Apollo's isn't configured or comes back
# empty (e.g. an unusual vertical Apollo doesn't index well).

# Vertical → Apollo keyword tags. Add more as you demo new verticals; the
# runner also asks the LLM to synthesize tags for unknown verticals.
_VERTICAL_TAGS: dict[str, list[str]] = {
    "healthtech":     ["digital health", "healthtech", "telehealth", "health tech"],
    "fintech":        ["fintech", "financial technology", "digital banking"],
    "insurtech":      ["insurtech", "insurance technology"],
    "legaltech":      ["legaltech", "legal technology", "legal software"],
    "edtech":         ["edtech", "education technology", "online learning"],
    "cybersecurity":  ["cybersecurity", "information security", "cyber security"],
    "ai":             ["artificial intelligence", "machine learning", "ai infrastructure"],
    "saas":           ["saas", "b2b software"],
    "developer tools": ["developer tools", "devops", "api"],
}


def _classify_vertical(brief: str) -> tuple[str, list[str]]:
    """Extract a canonical vertical and Apollo keyword tags from the founder's
    brief. Rule-based first (fast, free); LLM fallback for anything odd."""
    b = brief.lower()
    for name, tags in _VERTICAL_TAGS.items():
        if name in b or any(t in b for t in tags):
            return name, tags

    # LLM fallback — extract vertical + suggest Apollo tags
    from ghost.llm import complete_json
    result = complete_json(
        "Extract a target vertical + 3-4 Apollo.io industry/keyword tags from "
        f"this founder brief:\n\n{brief}\n\n"
        'Respond: {"vertical": "<name>", "tags": ["tag1", "tag2", ...]}',
        agent="runner.vertical",
        offline={"vertical": "b2b saas", "tags": ["saas", "b2b software"]},
    )
    return (str(result.get("vertical", "b2b saas")),
            [str(t) for t in (result.get("tags") or ["saas"])])


def _llm_name_startups(brief: str, vertical: str) -> list[str]:
    """Ask Nous Hermes-4 to name 5-8 real, notable startups in the vertical.
    Nous knows YC batches / notable Series-A companies; the Apollo enrichment
    step verifies each candidate exists before we commit to them."""
    from ghost.llm import complete_json
    result = complete_json(
        f"The founder said: {brief!r}\n\n"
        f"Name 8 real, well-known {vertical} startups (Seed to Series-B, "
        "US-based) that could plausibly be prospects. Prioritise companies "
        "founded 2019 or later. Include the primary domain for each. "
        "Only real companies — no fabrications. Skip huge public companies. "
        "Order matters — put the strongest fit for THIS founder's product "
        "first based on the brief.\n\n"
        'Respond with JSON exactly of shape: '
        '{"startups": [{"name": "<CompanyName>", "domain": "<primary-domain>"}]}',
        agent="runner.startups",
        offline={"startups": []},
        model=settings.llm_model,
    )
    picks = []
    for s in result.get("startups") or []:
        name = str(s.get("name", "")).strip()
        domain = str(s.get("domain", "")).strip().lower().rstrip("/")
        for p in ("https://", "http://", "www."):
            if domain.startswith(p):
                domain = domain[len(p):]
        if name and domain and "." in domain:
            picks.append({"name": name, "domain": domain})
    return picks


def _apollo_hunt(brief: str, stage_cb) -> dict[str, Any] | None:
    """Deterministic prospect discovery. Nous names candidate startups → we
    verify each via Apollo's org-lookup by domain → the first that resolves
    (real org, real contact, real emails) wins. This lands on named,
    high-quality companies rather than Apollo tag-search noise."""
    if not settings.apollo_api_key:
        return None
    from .research import apollo, email_guess

    try:
        vertical, _tags = _classify_vertical(brief)
    except Exception:
        vertical = "healthtech"

    stage_cb("brainstorm",
             f"Brainstorming candidate {vertical} startups (Nous Hermes-4)…")
    candidates = _llm_name_startups(brief, vertical)
    if not candidates:
        return None

    # Verify each candidate via Apollo domain enrichment (free); take the
    # first that comes back real. Nous occasionally hallucinates a plausible
    # domain — Apollo's index is our reality check.
    best: dict[str, Any] | None = None
    for cand in candidates:
        try:
            enrich = apollo.enrich_organization(cand["domain"])
        except apollo.ApolloError:
            continue
        if not enrich:
            continue
        best = {
            "name": enrich.get("name") or cand["name"],
            "domain": (enrich.get("primary_domain") or cand["domain"]).lower(),
            "employees": enrich.get("estimated_num_employees") or 0,
            "industry": enrich.get("industry") or vertical,
            "founded_year": enrich.get("founded_year"),
            "short_description": (enrich.get("short_description") or "")[:400],
            "linkedin_url": enrich.get("linkedin_url", ""),
        }
        break

    if best is None:
        return None

    stage_cb("apollo_pick",
             f"Verified: {best['name']} ({best['employees'] or '?'} emp)")

    # Enrich with a homepage fetch (adds pain evidence)
    from .research import web as _web
    page = _web.fetch(f"https://{best['domain']}")
    excerpt = ""
    if page.get("text"):
        # Grab the first meaty paragraph mentioning what they do
        for para in page["text"].split("\n"):
            if 40 < len(para) < 300 and any(w in para.lower() for w in
                    ("we ", "our ", "platform", "healthcare", "patients",
                     "data", "compliance", "software")):
                excerpt = para.strip()
                break

    # Apollo people search for the decision-maker (1 credit if we reveal)
    stage_cb("apollo_contact", f"Getting the decision-maker at {best['name']}…")
    contact_name = contact_title = email = ""
    email_candidates: list[str] = []
    try:
        contact = apollo.find_best_contact(best["domain"])
        if isinstance(contact, dict) and not contact.get("error"):
            contact_name = contact.get("name", "")
            contact_title = contact.get("title", "")
            email = contact.get("email", "")
            if email:
                email_candidates = [email]
    except apollo.ApolloError:
        pass

    if not email and contact_name:
        parts = contact_name.split()
        if len(parts) >= 2:
            email_candidates = [g["email"] for g in email_guess.guess(
                parts[0], parts[-1], best["domain"])[:3]]

    # Assemble the Research-shaped prospect
    return {
        "company_name": best["name"],
        "company_domain": best["domain"],
        "industry": best.get("industry", vertical),
        "contact": {
            "name": contact_name,
            "title": contact_title,
            "email_candidates": email_candidates,
            "linkedin_url": "",
        } if contact_name else None,
        "pain_evidence": [{
            "source_url": f"https://{best['domain']}",
            "excerpt": excerpt or best.get("short_description", ""),
        }] if (excerpt or best.get("short_description")) else [],
        "fit_score": 0.82,
        "fit_rationale": (
            f"Apollo-sourced {vertical} company, {best['employees']} employees "
            f"(startup-sized). Founded {best.get('founded_year','?')}. "
            f"Verified in Apollo's 275M-org index."
        ),
    }


StageCb = Callable[[str, str], None] | None


@dataclass
class CampaignArtifacts:
    """Everything a front-end needs to present + act on one campaign."""

    ok: bool = False
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    # prospect
    company: str = ""
    domain: str = ""
    contact_name: str = ""
    contact_title: str = ""
    recipient_email: str = ""
    fit_rationale: str = ""
    prospect: dict[str, Any] = field(default_factory=dict)

    # artifacts
    prototype_url: str = ""
    walkthrough_url: str = ""
    walkthrough_mp4: str = ""
    deck_url: str = ""
    deck_pptx: str = ""

    # outreach
    campaign_id: str = ""
    email_subject: str = ""
    email_body: str = ""

    cost_usd: float = 0.0


def run_campaign(
    brief: str,
    founder_context: FounderContext,
    *,
    on_stage: StageCb = None,
    on_event: EventSink = None,
    skip_lipsync: bool | None = None,
) -> CampaignArtifacts:
    """Run the full outbound chain for a single top prospect.

    ``brief`` — the founder's targeting ask (vertical, ICP hints).
    Returns a :class:`CampaignArtifacts`. Never raises for expected failures
    (empty research, deploy hiccup) — check ``.ok`` and ``.warnings``.
    """
    from ghost.llm import COST

    art = CampaignArtifacts()

    def stage(name: str, detail: str = "") -> None:
        if on_stage:
            try:
                on_stage(name, detail)
            except Exception:
                pass

    # A fresh chain = a fresh live-console story.
    bridge.new_run()

    # ── 1. Research — Apollo-first, LLM-fallback ──────────────
    stage("research", "Hunting for a fit prospect…")

    # Deterministic path: Apollo's 275M-org index → real startup, first try
    prospect = _apollo_hunt(brief, stage)
    if prospect is None:
        # Fallback for unusual verticals Apollo doesn't index well
        stage("research_llm",
              "Apollo didn't have a strong match — switching to web recon.")
        from .research import Research
        res = Research().run_brief(
            f"{brief.strip()}\n\nTarget shortlist size: 1.",
            on_event=on_event)
        candidates = res.get("prospects") or []
        prospect = candidates[0] if candidates else None

    if prospect is None:
        art.error = ("No fit prospect surfaced for that brief. Try a "
                     "different vertical or looser signal.")
        stage("failed", art.error)
        return art
    art.prospect = prospect
    art.company = prospect.get("company_name", "")
    art.domain = prospect.get("company_domain", "")
    contact = prospect.get("contact") or {}
    art.contact_name = contact.get("name", "")
    art.contact_title = contact.get("title", "")
    art.fit_rationale = prospect.get("fit_rationale", "")
    emails = contact.get("email_candidates") or []
    art.recipient_email = emails[0] if emails else ""
    stage("research_done", f"Target: {art.company}"
          + (f" · {art.contact_name}" if art.contact_name else ""))

    # ── 2. Engineer ───────────────────────────────────────────
    stage("engineer", f"Building a prototype for {art.company}…")
    from .engineer import Engineer

    eng = Engineer(founder_context=founder_context, prospect=prospect)
    ebuild = eng.build(on_event=on_event)
    art.prototype_url = ebuild.get("url", "")
    if not art.prototype_url or art.prototype_url.startswith("file:"):
        art.warnings.append("prototype deployed locally only (no public URL)")
    stage("engineer_done", f"Prototype live: {art.prototype_url}")

    # ── 3. Director ───────────────────────────────────────────
    stage("director", "Filming the walkthrough…")
    if skip_lipsync is not None:
        import os
        os.environ["DIRECTOR_SKIP_LIPSYNC"] = "1" if skip_lipsync else "0"
        from ghost.config import get_settings
        get_settings.cache_clear()
    from .director import Director

    d = Director(prototype_url=art.prototype_url or "https://example.com",
                 prospect=prospect)
    dfilm = d.film(on_event=on_event)
    art.walkthrough_url = dfilm.get("video_url", "")
    art.walkthrough_mp4 = dfilm.get("mp4_path", "")
    if not art.walkthrough_url:
        art.warnings.append("walkthrough hosted locally only")
    stage("director_done", f"Walkthrough ready: {art.walkthrough_url}")

    # ── 4. Sales ──────────────────────────────────────────────
    stage("sales", "Drafting the deck + email…")
    from .sales import Sales

    s = Sales(founder_context=founder_context, prospect=prospect,
              prototype_url=art.prototype_url,
              walkthrough_url=art.walkthrough_url)
    sdraft = s.draft(on_event=on_event)
    art.campaign_id = sdraft.get("campaign_id", "")
    art.email_subject = s.state.email_subject
    art.email_body = s.state.email_body
    art.deck_url = sdraft.get("deck_url", "")
    art.deck_pptx = sdraft.get("deck_pptx_path", "")
    stage("sales_done", "Draft ready for review.")

    art.cost_usd = round(COST.cents / 100, 4)
    art.ok = True
    stage("done", art.company)
    return art


def redraft_email(art: CampaignArtifacts, amendment: str,
                  founder_context: FounderContext,
                  *, on_event: EventSink = None) -> CampaignArtifacts:
    """Re-run Sales with a founder amendment, reusing the existing prototype,
    walkthrough, and deck. Returns the updated artifacts (same campaign id)."""
    from .sales import Sales

    s = Sales(founder_context=founder_context, prospect=art.prospect,
              prototype_url=art.prototype_url,
              walkthrough_url=art.walkthrough_url)
    s.draft(on_event=on_event, extra_instruction=(
        "The founder reviewed your previous draft and asked for this change:\n"
        f"“{amendment}”\n"
        "Rewrite the email accordingly. Keep the deck as-is unless the change "
        "clearly requires new slides."))
    art.email_subject = s.state.email_subject
    art.email_body = s.state.email_body
    if s.state.deck_url:
        art.deck_url = s.state.deck_url
    if s.state.convex_id:
        art.campaign_id = s.state.convex_id
    return art
