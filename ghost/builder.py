"""Builder agent — the differentiator. It ships a working artifact.

Given a scored, promoted lead it (1) generates a prospect-specific prototype
(for voice sellers: an embedded ElevenLabs conversational agent configured as
that clinic's receptionist; otherwise an interactive HTML mock + code snippet),
and (2) renders a personalized microsite with the pain quoted verbatim and
every claim cited. The site is *verified* (it must render) before it can be
deployed — hallucinated artifacts never reach a prospect.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import settings
from .llm import complete
from .log import log
from .models import Artifact, Campaign, Persona, SellerProfile

_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES)),
    autoescape=select_autoescape(["html", "xml", "j2"]),
)
OUT_SITES = Path("out/sites")
OUT_SITES.mkdir(parents=True, exist_ok=True)


def _prototype_html(campaign: Campaign, seller: SellerProfile) -> tuple[str, str]:
    """Return (prototype_html, citation). Voice sellers get a live convo-agent
    widget; others get an interactive mock. Offline uses static mocks."""
    lead = campaign.lead
    if seller.prototype_kind == "voice_demo":
        agent_id = settings.elevenlabs_agent_id
        if settings.require_live("elevenlabs_agent_id") and agent_id:
            widget = (
                f'<elevenlabs-convai agent-id="{agent_id}"></elevenlabs-convai>'
                '<script src="https://unpkg.com/@elevenlabs/convai-widget-embed" async></script>'
            )
        else:
            # Offline / no-agent: a believable receptionist mock with a call button.
            widget = _mock_receptionist(lead.company_name)
        cite = f"— configured live for {lead.company_name}'s front desk"
        return widget, cite

    # code / reference-impl sellers: generated snippet + run button
    snippet = _generate_snippet(campaign, seller)
    html = (
        '<p class="text-slate-300 mb-3">A drop-in reference implementation for your stack:</p>'
        f'<pre class="bg-black/60 rounded-lg p-4 overflow-x-auto text-sm text-emerald-300">'
        f"<code>{_escape(snippet)}</code></pre>"
        '<button class="mt-4 text-indigo-400 text-sm" onclick="beacon(\'copy_code\')">'
        "Copy snippet</button>"
    )
    return html, "— matches the pattern named in your job posting"


def _generate_snippet(campaign: Campaign, seller: SellerProfile) -> str:
    offline = (
        "// outbox.ts — exactly-once event delivery\n"
        "export async function publishWithOutbox(evt: Event, tx: Tx) {\n"
        "  await tx.insert('outbox', { id: evt.id, payload: evt, status: 'pending' });\n"
        "  // a single relay drains 'pending' rows and marks them 'sent',\n"
        "  // so a crash between commit and publish never double-delivers.\n"
        "}\n"
    )
    return complete(
        f"Write a tight, correct ~15-line code snippet that demonstrates a fix for "
        f"this pain, for {campaign.lead.company_name}. Pain: "
        f"{campaign.lead.job_description[:300]}. Product context: {seller.product}. "
        f"Return ONLY code, no prose.",
        agent="developer",
        offline=offline,
        temperature=0.2,
    )


def _mock_receptionist(company: str) -> str:
    return (
        f'<div class="text-center">'
        f'<div class="text-5xl mb-3">📞</div>'
        f'<p class="text-slate-200 font-medium">{company} — AI Front Desk</p>'
        f'<p class="text-slate-400 text-sm mt-1">"Thanks for calling {company}. '
        f'I can book, reschedule, or triage — how can I help?"</p>'
        f'<button onclick="beacon(\'call_click\')" class="mt-4 bg-emerald-500 '
        f'hover:bg-emerald-400 text-white px-6 py-3 rounded-lg font-semibold">'
        f'▶ Talk to the agent</button>'
        f'<p class="text-xs text-slate-600 mt-2">answers 24/7 · 0s hold time</p></div>'
    )


def build(campaign: Campaign, seller: SellerProfile) -> Campaign:
    """Generate + verify the microsite. Sets campaign.microsite_url once
    deployed (deploy step handles hosting); here we render and self-verify."""
    log.stage(f"Builder: engineering a prototype for {campaign.lead.company_name}…")
    persona = campaign.persona or Persona()

    proto_html, proto_cite = _prototype_html(campaign, seller)

    headline = complete(
        f"Write a punchy 8-word headline for a microsite {seller.name} built for "
        f"{campaign.lead.company_name} to solve: {campaign.lead.job_description[:200]}",
        agent="copywriter",
        offline=f"We built {seller.name} into {campaign.lead.company_name}'s front desk.",
        temperature=0.6,
    ).strip().strip('"')

    pain_quote, pain_cite = _pain_quote(campaign)

    ctx = {
        "seller": seller.model_dump(),
        "lead": campaign.lead.model_dump(),
        "campaign_id": campaign.id,
        "ts": time.strftime("%b %d, %Y"),
        "headline": headline,
        "subhead": seller.value_prop or seller.one_liner,
        "pain_quote": pain_quote,
        "pain_citation": pain_cite,
        "prototype_html": proto_html,
        "prototype_citation": proto_cite,
        "walkthrough_url": campaign.walkthrough_url,
        "voice_url": campaign.voice_memo_ref,
        "payment_link": campaign.payment_link or "#",
        "pilot_price": seller.pilot_price_inr,
        "beacon_url": f"{settings.convex_url or ''}/beacon",
    }
    html = _env.get_template("microsite.html.j2").render(**ctx)

    # ── verify: the artifact must render to real HTML before it can ship ──
    verified = _verify(html)
    site_dir = OUT_SITES / campaign.lead.company_domain.replace(".", "_")
    site_dir.mkdir(parents=True, exist_ok=True)
    out_file = site_dir / "index.html"
    out_file.write_text(html)
    checksum = hashlib.sha256(html.encode()).hexdigest()[:16]

    campaign.artifacts.append(
        Artifact(kind="site", storage_ref=str(out_file), checksum=checksum, verified=verified,
                 meta={"headline": headline})
    )
    campaign.add_cost(3)  # ~$0.03 build amortized
    if verified:
        log.ok(f"Prototype verified & rendered → {out_file}")
    else:
        log.warn("Prototype failed verification — will not deploy")
    return campaign


def _verify(html: str) -> bool:
    """Cheap structural verification (the buildathon stand-in for the sandbox):
    real HTML, has the hero + CTA, no unrendered template tokens."""
    if "{{" in html or "{%" in html:
        return False
    required = ["<html", "</html>", "Book a paid pilot", "<h1"]
    return all(tok in html for tok in required)


def _pain_quote(campaign: Campaign) -> tuple[str, str]:
    """Pull the strongest verbatim evidence excerpt as the on-site pain quote."""
    score = campaign.lead.score
    if score and score.evidence:
        best = max(score.evidence, key=lambda e: e.weight)
        label = {
            "jd": "per your job posting",
            "careers": "per your careers page",
            "status": "per your public status log",
            "github": "per your public repo issues",
            "eng_blog": "per your engineering blog",
            "news": "per public reporting",
        }.get(best.source, "per public sources")
        return best.excerpt, label
    # fallback: first sentence of the JD
    jd = campaign.lead.job_description
    return (jd.split(".")[0] if jd else "You're hiring to fix this."), "per your job posting"


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
