"""Built-in seller configurations + the onboarding path.

A ``SellerProfile`` is the single swap point that re-targets the whole pipeline
at a new vertical. Two configs ship in-repo:

* ``queuepilot`` — support-triage AI for any team drowning in tickets.
* ``echodesk`` — voice AI for healthcare.
* ``ledgerloop`` — a generic dev-tools seller, to prove "configurable" live.

:func:`onboard` turns a free-text dictated blurb (Wispr Flow → Hermes) into a
structured profile via the LLM, so a founder can stand up a brand-new seller on
stage in one sentence.
"""

from __future__ import annotations

from .llm import complete_json
from .models import SellerProfile

_BUILTINS: dict[str, SellerProfile] = {
    "queuepilot": SellerProfile(
        slug="queuepilot",
        name="QueuePilot AI",
        one_liner="AI support triage that turns messy inbound tickets into routed, prioritized work.",
        product="A support-ops copilot that reads public symptoms from job posts, issues, "
        "and support queues, then builds a live ticket triage workspace with SLA risk, "
        "routing, summaries, and suggested response macros.",
        icp="B2B SaaS, marketplaces, healthcare ops, fintech, devtools, and ecommerce "
        "teams with growing customer support volume, manual ticket routing, SLA risk, "
        "or Zendesk/Intercom backlog pressure.",
        pain_keywords=[
            "support backlog",
            "ticket triage",
            "SLA breach",
            "manual routing",
            "Zendesk backlog",
            "customer escalation",
        ],
        prototype_kind="support_triage",
        value_prop="Cut first-response time and escalation chaos by auto-routing every inbound issue with evidence, priority, and a ready-to-send reply.",
        pilot_price_inr=7999,
    ),
    "echodesk": SellerProfile(
        slug="echodesk",
        name="EchoDesk AI",
        one_liner="Voice AI that answers every patient call so no appointment slips.",
        product="An AI front-desk agent for healthcare: books, reschedules, triages, "
        "and routes patient calls 24/7 — integrates with existing phone systems.",
        icp="Multi-location clinics, dental groups, and hospital networks drowning in "
        "front-desk call volume and appointment no-shows.",
        pain_keywords=[
            "front desk call volume",
            "patient wait time",
            "appointment scheduling backlog",
            "call drop rate",
            "receptionist hiring",
            "phone hold time",
        ],
        prototype_kind="voice_demo",
        value_prop="Cut hold times to zero and recover no-show revenue with an AI "
        "receptionist that never sleeps.",
        pilot_price_inr=4999,
    ),
    "ledgerloop": SellerProfile(
        slug="ledgerloop",
        name="LedgerLoop",
        one_liner="Exactly-once event delivery so your payments never double-charge.",
        product="A drop-in outbox + idempotency service for event-driven backends.",
        icp="Series-A fintech and marketplace teams scaling event pipelines who are "
        "fighting duplicate deliveries and lost events.",
        pain_keywords=[
            "reliable event delivery",
            "idempotency",
            "outbox pattern",
            "kafka connect",
            "exactly once",
            "duplicate webhook",
        ],
        prototype_kind="reference_impl",
        value_prop="Ship reliable event delivery in an afternoon instead of a quarter.",
        pilot_price_inr=9999,
    ),
}


def get_seller(slug: str) -> SellerProfile:
    if slug in _BUILTINS:
        return _BUILTINS[slug].model_copy(deep=True)
    raise KeyError(f"unknown seller '{slug}'. Known: {', '.join(_BUILTINS)}")


def list_sellers() -> list[str]:
    return list(_BUILTINS)


def onboard(blurb: str, *, slug: str = "custom") -> SellerProfile:
    """Dictated company description → structured SellerProfile.

    This is the ``ghost-onboard`` skill's core. Offline mode falls back to a
    reasonable generic profile so the flow is always demoable.
    """
    out = complete_json(
        f"A founder just described their company by voice. Extract a structured "
        f"seller profile for an autonomous outbound engine.\n\nDescription:\n{blurb}",
        agent="onboard",
        system=(
            "Return JSON: {name, one_liner, product, icp, pain_keywords (6 short "
            "phrases a prospect's job posts would contain), prototype_kind (one of "
            "benchmark|diagnostic|reference_impl|config_diff|voice_demo), value_prop, "
            "pilot_price_inr (int)}."
        ),
        offline={
            "name": blurb.split(".")[0][:40] or "Custom Seller",
            "one_liner": blurb[:80],
            "product": blurb[:200],
            "icp": "Teams feeling this pain acutely enough to hire for it.",
            "pain_keywords": blurb.lower().split()[:6],
            "prototype_kind": "reference_impl",
            "value_prop": "Solve it before the first call.",
            "pilot_price_inr": 4999,
        },
    )
    return SellerProfile(
        slug=slug,
        name=out.get("name", "Custom Seller"),
        one_liner=out.get("one_liner", ""),
        product=out.get("product", ""),
        icp=out.get("icp", ""),
        pain_keywords=list(out.get("pain_keywords", []))[:6] or ["scaling pain"],
        prototype_kind=out.get("prototype_kind", "reference_impl"),
        value_prop=out.get("value_prop", ""),
        pilot_price_inr=int(out.get("pilot_price_inr", 4999)),
    )
