"""The persistence engine fires against a past-due memory (plan verification)."""

from __future__ import annotations

import time

from ghost import followup
from ghost.ledger import ledger
from ghost.models import Campaign, CampaignState, Lead


def _campaign(state=CampaignState.SENT) -> Campaign:
    lead = Lead(seller_id="sel_x", company_name="Acme Clinics",
                company_domain="acme.example", person_name="Dr. Vega")
    return Campaign(seller_id="sel_x", lead=lead, state=state)


def test_due_memory_reopens_campaign() -> None:
    camp = _campaign()
    ledger.upsert_campaign(camp)
    # prospect said "ping me in Q3" — the window closed yesterday
    ledger.remember(camp, kind="constraint",
                    body="Budget unlocks in Q3; re-ping July 1.",
                    re_ping_at=time.time() - 86_400)

    reopened = followup.scan()

    assert any(c.id == camp.id for c in reopened)
    assert camp.state == CampaignState.AWAITING_REVIEW
    # the follow-up must reference the specific commitment, not be generic
    assert "Budget unlocks" in camp.email_subject


def test_won_deal_never_reengaged() -> None:
    camp = _campaign(state=CampaignState.WON)
    ledger.upsert_campaign(camp)
    ledger.remember(camp, kind="commitment", body="Paid pilot.",
                    re_ping_at=time.time() - 10)

    reopened = followup.scan()

    assert all(c.id != camp.id for c in reopened)
    assert camp.state == CampaignState.WON
