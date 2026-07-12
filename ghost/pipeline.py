"""The orchestration graph — the spine everything hangs off.

Runs one seller's campaign lifecycle end to end: recon → gate → (route by tier)
→ profiler → builder → deploy → director → voice → payments → outreach draft →
awaiting_review. State transitions are persisted to the ledger after every step
so a crash resumes where it stopped (conceptually — buildathon runs in-process).

This mirrors the master-plan graph (§5) with the Addendum-001 signal gate as
the routing front-door. It is intentionally sequential and readable; the Go
concurrency model from the master plan is out of scope for the buildathon.
"""

from __future__ import annotations

from . import builder, deploy, director, outreach, payments, profiler, recon, voice
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
        log.kill(f"  killed for ~$0.001 — boilerplate, no spend")
        ledger.set_state(camp, CampaignState.KILLED)
        return camp

    if score.tier in (Tier.WARM_ONLY,):
        # soft path: draft a warm intro, no engineered artifact
        camp.persona = profiler.profile(lead, seller)
        outreach.draft(camp, seller)
        ledger.set_state(camp, CampaignState.WARM_ONLY)
        log.warn("  warm-only — soft touch, no prototype")
        return camp

    # ── promote (and corroborate, which we treat as promote at v0) ──
    camp.persona = profiler.profile(lead, seller)

    ledger.set_state(camp, CampaignState.BUILDING)
    builder.build(camp, seller)

    ledger.set_state(camp, CampaignState.DEPLOYED)
    deploy.deploy(camp)

    payments.create_payment_link(camp, seller)

    ledger.set_state(camp, CampaignState.FILMING)
    director.direct(camp, seller)

    voice.synthesize(camp, seller)

    # re-render the site now that walkthrough + voice + payment exist
    builder.build(camp, seller)
    deploy.deploy(camp)

    outreach.draft(camp, seller)
    ledger.set_state(camp, CampaignState.AWAITING_REVIEW)
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
