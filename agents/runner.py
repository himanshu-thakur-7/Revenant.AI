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

from .base import EventSink
from .bridge import bridge
from .context import FounderContext


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

    # ── 1. Research ───────────────────────────────────────────
    stage("research", "Hunting for a fit prospect…")
    from .research import Research

    r = Research()
    res = r.run_brief(f"{brief.strip()}\n\nTarget shortlist size: 1.",
                      on_event=on_event)
    prospects = res.get("prospects") or []
    if not prospects:
        art.error = ("Research found no fit prospect for that brief. "
                     "Try a broader vertical or different signals.")
        stage("failed", art.error)
        return art

    prospect = prospects[0]
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
