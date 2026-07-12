"""The orchestration graph — the spine everything hangs off.

Runs one seller's campaign lifecycle end to end: recon → gate → (route by tier)
→ profiler → builder → deploy → director → voice → payments → outreach draft →
awaiting_review. State transitions persist to the ledger after every step, and
every move emits a mission-log event so the console can replay the run agent
by agent — the storyline, visible.
"""

from __future__ import annotations

from . import builder, deploy, director, outreach, payments, profiler, recon, voice
from .events import GATEKEEPER, OUTREACH, PROFILER, mission
from .gate import evaluate
from .ledger import ledger
from .log import log
from .models import Campaign, CampaignState, Lead, SellerProfile, SignalScore, Tier


def run_seller(seller: SellerProfile, limit: int = 3) -> list[Campaign]:
    """Full hunt→review loop for one seller. Returns the campaigns produced."""
    ledger.upsert_seller(seller)
    log.info(f"\n[bold]🕯️  Revenant — hunting for {seller.name}[/bold]\n")

    leads = recon.hunt(seller, limit=limit)
    campaigns: list[Campaign] = []

    for lead, forensics in leads:
        camp = _run_one(seller, lead, forensics)
        campaigns.append(camp)

    _summary(seller, campaigns)
    ledger.publish_events()
    return campaigns


def _run_one(seller: SellerProfile, lead: Lead, forensics: SignalScore) -> Campaign:
    camp = Campaign(seller_id=seller.id, lead=lead, state=CampaignState.SCOUTING)
    ledger.upsert_campaign(camp)

    # ── gate: score & route (the token-budget guard) ──────────
    score = evaluate(lead.job_description, forensics)
    lead.score = score
    camp.add_cost(0.1)  # gate is ~$0.001
    ledger.set_state(camp, CampaignState.SCORED)
    log.info(
        f"  gate: [bold]{lead.company_name}[/bold] → "
        f"{score.tier.value} ({score.combined:.2f})"
    )

    if score.tier == Tier.KILL:
        mission.emit(
            2, GATEKEEPER,
            f"{lead.company_name}: HR boilerplate, zero technical signal. KILLED for "
            f"$0.001 — not one expensive token spent.",
            campaign_id=camp.id, company=lead.company_name, kind="verdict", dwell=2.2,
            payload={"tier": "kill", "score": round(score.combined, 2), "state": "killed"},
        )
        log.kill("  killed for ~$0.001 — boilerplate, no spend")
        ledger.set_state(camp, CampaignState.KILLED)
        return camp

    if score.tier in (Tier.WARM_ONLY,):
        mission.emit(
            2, GATEKEEPER,
            f"{lead.company_name}: pain is real but thin ({score.combined:.2f}). Routed "
            f"WARM-ONLY — a soft intro, no engineered artifact. We never fake a prototype.",
            campaign_id=camp.id, company=lead.company_name, kind="verdict", dwell=2.2,
            payload={"tier": "warm_only", "score": round(score.combined, 2), "state": "warm_only"},
        )
        camp.persona = profiler.profile(lead, seller)
        outreach.draft(camp, seller)
        ledger.set_state(camp, CampaignState.WARM_ONLY)
        log.warn("  warm-only — soft touch, no prototype")
        return camp

    # ── promote (and corroborate, which we treat as promote at v0) ──
    mission.emit(
        2, GATEKEEPER,
        f"{lead.company_name}: VERIFIED HIGH-VALUE TARGET ({score.tier.value}, "
        f"{score.combined:.2f}). {len(score.evidence)} pieces of cited evidence. "
        f"Dispatching the swarm.",
        campaign_id=camp.id, company=lead.company_name, kind="verdict", dwell=2.4,
        payload={"tier": score.tier.value, "score": round(score.combined, 2)},
    )
    mission.emit(
        2, PROFILER,
        f"Reading {lead.person_name or 'the decision-maker'} ({lead.person_title}) — "
        f"public writing only. Scoring tone axes for the pitch voice.",
        campaign_id=camp.id, company=lead.company_name, kind="info", dwell=2.0,
    )
    camp.persona = profiler.profile(lead, seller)
    p = camp.persona
    mission.emit(
        2, PROFILER,
        f"Persona locked: {'formal' if p.casual_formal > 0.55 else 'casual'}, "
        f"{'technical' if p.technical_strategic > 0.5 else 'strategic'}, "
        f"{'direct' if p.warm_blunt > 0.55 else 'warm'}. "
        f"Callback material: {p.references[0] if p.references else 'n/a'}",
        campaign_id=camp.id, company=lead.company_name, kind="info", dwell=2.0,
    )

    ledger.set_state(camp, CampaignState.BUILDING)
    builder.build(camp, seller)

    ledger.set_state(camp, CampaignState.DEPLOYED)
    deploy.deploy(camp)

    payments.create_payment_link(camp, seller)

    ledger.set_state(camp, CampaignState.FILMING)
    director.direct(camp, seller)

    voice.synthesize(camp, seller)

    # re-render the site now that walkthrough + voice + payment exist
    builder.build(camp, seller, quiet=True)
    deploy.deploy(camp, quiet=True)

    outreach.draft(camp, seller)
    ledger.set_state(camp, CampaignState.AWAITING_REVIEW)
    mission.emit(
        5, OUTREACH,
        f"{lead.company_name} package complete — live prototype, AI walkthrough, voice "
        f"memo, pilot link. Parked in the review queue. Nothing sends without a human click.",
        campaign_id=camp.id, company=lead.company_name, kind="state", dwell=2.4,
        payload={"state": "awaiting_review"},
    )
    log.ok(f"  {lead.company_name} → awaiting_review "
           f"(${camp.cost_cents/100:.2f} all-in)")
    return camp


def _summary(seller: SellerProfile, campaigns: list[Campaign]) -> None:
    from .llm import COST

    by_state: dict[str, int] = {}
    for c in campaigns:
        s = c.state.value
        by_state[s] = by_state.get(s, 0) + 1
    total = sum(c.cost_cents for c in campaigns) / 100
    log.info("\n[bold]── Funnel ──[/bold]")
    for state, n in by_state.items():
        log.info(f"  {state:16} {n}")
    log.info(f"\n  total spend: [bold]${total:.2f}[/bold] across {len(campaigns)} leads")
    log.info(f"  LLM calls: {COST.calls}  ·  tokens: {COST.in_tokens+COST.out_tokens:,}")
    ready = [c for c in campaigns if c.state == CampaignState.AWAITING_REVIEW]
    if ready:
        log.info("\n[bold green]Ready for review:[/bold green]")
        for c in ready:
            log.info(f"  • {c.lead.company_name}: {c.microsite_url}")
