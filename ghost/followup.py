"""The persistence engine — re-engagement based on remembered commitments.

Mirrors the master-plan re-engagement scheduler and the Convex nightly cron.
A prospect who said "ping me in Q3" gets a memory row with a ``re_ping_at``
timestamp; when that window closes, this scan re-opens the campaign into the
review queue with the outreach forced to reference the *specific* prior
commitment — never a generic "just circling back".
"""

from __future__ import annotations

import time

from .ledger import ledger
from .log import log
from .models import Campaign, CampaignState


def scan(now: float | None = None) -> list[Campaign]:
    """Re-open every campaign whose memory deferral window has closed."""
    now = now if now is not None else time.time()
    due = ledger.due_memories(now)
    if not due:
        log.dim("[followup] no memories due")
        return []

    reopened: list[Campaign] = []
    by_id = {c.id: c for c in ledger.campaigns()}
    for mem in due:
        camp = by_id.get(mem["campaign_id"])
        if not camp or camp.state == CampaignState.WON:
            continue
        camp.notes.append(f"re-engagement: {mem['body']}")
        # Force the reference to the specific commitment into the next draft.
        camp.email_subject = f"Following up on: {mem['body'][:48]}"
        ledger.set_state(camp, CampaignState.AWAITING_REVIEW)
        # consume the trigger so it doesn't re-fire
        mem["re_ping_at"] = None
        reopened.append(camp)
        log.ok(f"[followup] re-opened {camp.lead.company_name}: {mem['body']}")

    ledger._flush()  # persist the consumed triggers
    log.ok(f"[followup] re-engaged {len(reopened)} prospect(s)")
    return reopened
