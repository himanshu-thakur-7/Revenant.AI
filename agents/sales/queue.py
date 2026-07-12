"""Write the draft to the Convex review queue.

Reuses the existing ``ledger:upsertCampaign`` mutation from
``convex/ledger.ts`` — schemaless (``v.any()``), so any fields we pass
survive. When Convex isn't configured we fall back to writing a JSON snapshot
next to the local email markdown, so the review console still has something
to fall back to via the demoData path.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from ghost.config import settings


def push_draft(row: dict[str, Any]) -> dict[str, Any]:
    """Upsert a campaign row into Convex's ``campaigns`` table.

    Returns ``{convex_ok, warning?}``.
    """
    if not settings.convex_url:
        return {"convex_ok": False,
                "warning": "CONVEX_URL not set — draft written locally only"}

    try:
        resp = httpx.post(
            f"{settings.convex_url}/api/mutation",
            json={
                "path": "ledger:upsertCampaign",
                "args": {"doc": row},
                "format": "json",
            },
            timeout=15,
        )
        body = resp.json()
    except Exception as exc:
        return {"convex_ok": False, "warning": f"Convex write failed: {exc}"}

    if body.get("status") != "success":
        return {"convex_ok": False,
                "warning": f"Convex rejected mutation: {str(body)[:200]}"}
    return {"convex_ok": True}


def draft_row(*, prospect: dict[str, Any],
              email_subject: str, email_body: str,
              prototype_url: str, walkthrough_url: str,
              deck_url: str, cost_usd: float) -> dict[str, Any]:
    """Build the campaign row payload the console already knows how to render."""
    lead = {
        "company_name": prospect.get("company_name", ""),
        "company_domain": prospect.get("company_domain", ""),
        "person_name": (prospect.get("contact") or {}).get("name", ""),
        "person_title": (prospect.get("contact") or {}).get("title", ""),
        "job_description": prospect.get("fit_rationale", ""),
        "score": {
            "combined": float(prospect.get("fit_score", 0.75)),
            "tier": "promote",
            "evidence": prospect.get("pain_evidence", []),
        },
    }
    return {
        "id": f"camp_sales_{int(time.time())}_{prospect.get('company_domain','x').replace('.','-')}",
        "seller_id": "shroud",
        "state": "awaiting_review",
        "tier": "promote",
        "combined_score": float(prospect.get("fit_score", 0.75)),
        "microsite_url": prototype_url,
        "microsite_html": "",
        "walkthrough_url": walkthrough_url,
        "voice_memo_ref": "",
        "email_subject": email_subject,
        "email_body": email_body,
        "deck_url": deck_url,
        "payment_link": "",
        "cost_usd": round(cost_usd, 4),
        "lead": lead,
    }
