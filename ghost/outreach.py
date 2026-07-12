"""Outreach — draft the email, park it for human review, send on approval.

The email carries the two artifacts that make this different: the live microsite
link and the AI-recorded walkthrough. ``DRY_RUN`` is on by default; real sends
go through Resend and are gated to team-owned inboxes during the buildathon.
The persistence engine (follow-ups) lives in ``skills/ghost-followup`` + Convex
cron; here we only handle the first touch.
"""

from __future__ import annotations

import httpx

from .config import settings
from .events import OUTREACH, mission
from .llm import complete_json
from .log import log
from .models import Campaign, Persona, SellerProfile


def draft(campaign: Campaign, seller: SellerProfile) -> Campaign:
    log.stage(f"Outreach: drafting the email to {campaign.lead.person_name or 'the exec'}…")
    lead = campaign.lead
    persona = campaign.persona or Persona()
    ref = persona.references[0] if persona.references else ""

    offline = {
        "subject": f"Built {seller.name} into {lead.company_name}'s front desk (2-min look)",
        "body": (
            f"Hi {lead.person_name or 'there'},\n\n"
            f"{'I saw ' + ref + '. ' if ref else ''}Rather than pitch you, I had our "
            f"system build a working prototype for {lead.company_name} and record a "
            f"quick walkthrough of it.\n\n"
            f"▶ Watch the 60-sec walkthrough: {campaign.walkthrough_url}\n"
            f"🔗 Try the live prototype: {campaign.microsite_url}\n\n"
            f"{seller.value_prop}\n\n"
            f"If it's useful, there's a one-click pilot on the page. If not, just reply "
            f"STOP and you'll never hear from us again.\n\n"
            f"— The {seller.name} team"
        ),
    }

    if settings.require_live("llm_api_key"):
        out = complete_json(
            f"Write a short, human cold email from {seller.name} to {lead.person_name} "
            f"({lead.person_title} at {lead.company_name}). Reference the walkthrough "
            f"video ({campaign.walkthrough_url}) and live prototype "
            f"({campaign.microsite_url}). Tone: "
            f"{'formal' if persona.casual_formal > 0.6 else 'casual'}, "
            f"{'warm' if persona.warm_blunt < 0.5 else 'direct'}. Include an unsubscribe line.",
            agent="outreach",
            system="Return {subject, body}. No hype, no 'I hope this finds you well'.",
            offline=offline,
        )
    else:
        out = offline

    campaign.email_subject = out.get("subject", offline["subject"])
    campaign.email_body = out.get("body", offline["body"])
    campaign.add_cost(1)
    mission.emit(
        5, OUTREACH,
        f"Drafted for {lead.person_name or 'the decision-maker'}: “{campaign.email_subject}” — "
        f"carries the live deployment link and the AI walkthrough. Precision, not spray.",
        campaign_id=campaign.id, company=lead.company_name, kind="mail", dwell=2.2,
    )
    log.ok("Email drafted → awaiting human review")
    return campaign


def send(campaign: Campaign, to_email: str) -> bool:
    """Send the approved email. Honors DRY_RUN (default on)."""
    if settings.dry_run:
        log.warn(f"[outreach] DRY_RUN — would send to {to_email}: {campaign.email_subject!r}")
        return False
    if not settings.require_live("resend_api_key"):
        log.warn("[outreach] no Resend key; not sending")
        return False
    return _resend(campaign, to_email)


def _resend(campaign: Campaign, to_email: str) -> bool:  # pragma: no cover - network
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "from": settings.from_email,
                "to": [to_email],
                "subject": campaign.email_subject,
                "text": campaign.email_body,
            },
            timeout=20,
        )
        resp.raise_for_status()
        log.ok(f"[outreach] sent to {to_email}")
        return True
    except Exception as exc:
        log.warn(f"[outreach] Resend failed: {exc!r}")
        return False
